"""The worker that drives a generation job to a terminal state.

The queue is a table, so a job survives a restart. This loop is the only place
that contacts a provider about an existing job, and it obeys three rules that
matter more than throughput:

1. A job that already has a provider job id is never submitted again. Resuming
   means resuming the poll, because a second submit is a second charge.
2. A state change is written before the next side effect, so a crash leaves the
   database describing the last thing that really happened.
3. Progress is reported only when the provider reported it. There is no
   interpolation and no invented percentage.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..adapters.core import TERMINAL_STATES, get_adapter
from ..adapters.types import GenerationRequest, JobStatus, ProviderOutput, SubmittedJob
from ..errors import (
    CAPABILITY_UNSUPPORTED,
    CONFIGURATION_REQUIRED,
    RETRYABLE_CODES,
    TIMEOUT,
    ApiError,
)
from ..logging_setup import get_logger
from ..redact import scrub_structure
from ..util import json_dumps, json_loads, new_id, now_iso

LOG = get_logger(__name__)

#: States a worker may still act on.
ACTIVE_STATES = ("queued", "validating", "submitting", "processing", "downloading")


@dataclass
class WorkerStats:
    """What one worker has done since it started. Reported by the health route."""

    claimed: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    errors: int = 0
    last_error: str = ""
    last_activity: str = ""
    started_at: str = field(default_factory=now_iso)

    def to_public(self) -> Dict[str, Any]:
        """A small dict for the status endpoint."""
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "errors": self.errors,
            "last_error": self.last_error,
            "last_activity": self.last_activity,
            "started_at": self.started_at,
        }


class GenerationRunner:
    """Claim jobs, drive them, and record what actually happened.

    Constructed once per process. The worker threads share it; all mutable state
    is either a database row or guarded by the claim itself.
    """

    def __init__(
        self,
        *,
        repositories: Any,
        connections: Any,
        assets: Any,
        config: Any,
        worker_id: str = "",
    ) -> None:
        self.repos = repositories
        self.connections = connections
        self.assets = assets
        self.config = config
        self.worker_id = worker_id or "worker-" + new_id()[:8]
        self.stats = WorkerStats()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------------

    def start(self, threads: int = 0) -> int:
        """Start the worker threads. Returns how many were started.

        Zero threads is a valid configuration: the web process serves requests
        and a separate process runs the same object with workers above zero.
        """
        count = threads if threads else self.config.jobs.workers
        if count <= 0:
            LOG.info("generation worker not started in this process")
            return 0
        for index in range(count):
            name = "%s-%d" % (self.worker_id, index)
            thread = threading.Thread(target=self._loop, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        LOG.info("generation worker started", extra={"threads": count})
        return count

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the threads to finish the current step and exit."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    @property
    def running(self) -> bool:
        """True while at least one worker thread is alive."""
        return any(thread.is_alive() for thread in self._threads)

    # -- the loop --------------------------------------------------------------

    def _loop(self) -> None:
        """Claim, drive, release. Sleeps when there is nothing to do."""
        interval = max(0.2, float(self.config.jobs.poll_interval_seconds))
        while not self._stop.is_set():
            did_work = False
            try:
                job = self.repos.jobs.claim_next(
                    self.worker_id, lease_seconds=self.config.jobs.lease_seconds
                )
            except Exception as error:  # noqa: BLE001 - a broken claim must not kill the loop
                self.stats.errors += 1
                self.stats.last_error = "%s: %s" % (type(error).__name__, error)
                LOG.exception("claiming a job failed")
                job = None
                self._stop.wait(interval * 4)
                continue
            if job is None:
                self._stop.wait(interval)
                continue
            self.stats.claimed += 1
            self.stats.last_activity = now_iso()
            try:
                self.drive(job)
            except Exception as error:  # noqa: BLE001
                self.stats.errors += 1
                self.stats.last_error = "%s: %s" % (type(error).__name__, error)
                LOG.exception("unhandled runner failure", extra={"job_id": job.get("id")})
                self._fail(job, ApiError(
                    "internal_error",
                    "The worker hit an unexpected condition while running this job.",
                    provider_message=type(error).__name__,
                ))
            # There was work, so the loop goes straight back for the next job. The
            # idle sleep is the `job is None` branch above; a job that has been
            # polled but is still running comes back once its lease lapses, and a
            # short pause here keeps that from being a spin.
            self._stop.wait(interval)

    # -- one job ----------------------------------------------------------------

    def drive(self, job: Dict[str, Any]) -> None:
        """Move one claimed job as far as it will go in this pass.

        Called by the loop and directly by tests. It is idempotent with respect to
        the provider: a job that already carries a provider job id resumes at the
        poll, never at the submit.
        """
        job_id = str(job.get("id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        if job.get("cancel_requested_at"):
            self._cancel(job, message="Cancelled before the request was sent.")
            return
        request = self._build_request(job)
        connection = self._connection(job)
        adapter = get_adapter(str(connection.get("adapter_type") or ""))
        profile = self._profile(job)
        try:
            adapter.validate_input(request, profile)
        except ApiError as error:
            self._fail(job, error)
            return
        if not job.get("provider_job_ref"):
            self._submit(job, adapter, connection, request, profile)
            return
        self._poll_until_done(job, adapter, connection, request)

    # -- phases -----------------------------------------------------------------

    def _submit(
        self,
        job: Dict[str, Any],
        adapter: Any,
        connection: Dict[str, Any],
        request: GenerationRequest,
        profile: Any,
    ) -> None:
        """Send the request exactly once, then poll until it settles."""
        job_id = str(job.get("id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        # queued -> validating -> submitting is the recorded sequence, so the
        # history shows the request was checked before it was sent.
        self._transition(job, "validating", "Checking the request against this model's capabilities.")
        self._transition(job, "submitting", "Sending the request to the provider.")
        if job.get("cancel_requested_at"):
            self._cancel(job, message="Cancelled before the request was sent.")
            return
        context = self.connections.context_for(connection)
        try:
            submitted = adapter.submit(context, request)
        except ApiError as error:
            self._handle_submit_error(job, error)
            return
        self.repos.connections.touch_used(workspace_id, str(connection.get("id") or ""))
        self._record_usage(job, connection, request, provider_ref=submitted.provider_job_id)
        if submitted.outputs and submitted.raw_status == "completed":
            # A synchronous provider: there is nothing left to poll. The state
            # still walks the queue's own sequence, so the history reads the same
            # whether the provider was fast or slow.
            self._transition(
                job,
                "processing",
                "The provider accepted the request.",
                provider_job_id=submitted.provider_job_id,
            )
            self._transition(
                job,
                "downloading",
                "The provider returned the output immediately.",
            )
            self._store_outputs(job, list(submitted.outputs))
            return
        self._transition(
            job,
            "processing",
            "The provider accepted the request.",
            provider_job_id=submitted.provider_job_id,
        )
        self._remember_routes(job, submitted)
        self._poll_until_done(job, adapter, connection, request, submitted)

    def _poll_until_done(
        self,
        job: Dict[str, Any],
        adapter: Any,
        connection: Dict[str, Any],
        request: Optional[GenerationRequest] = None,
        submitted: Optional[SubmittedJob] = None,
    ) -> None:
        """Poll once per pass. Long waits are the loop responsibility, not this call.

        Polling a provider inside a single request would make a slow model look
        like a hung server, so one poll happens here and the state is written.
        The next pass picks the job up again.
        """
        stored = self.repos.jobs.get(str(job.get("workspace_id") or ""), str(job.get("id") or ""))
        submitted = submitted or SubmittedJob(
            provider_job_id=str(stored.get("provider_job_ref") or ""),
            status_url=self._route(stored, "status_url"),
            result_url=self._route(stored, "result_url"),
            raw_status=str(stored.get("provider_status") or "processing"),
        )
        if not submitted.provider_job_id and not submitted.outputs:
            self._fail(
                job,
                ApiError(
                    "invalid_response",
                    "This job has no provider reference, so it cannot be tracked or resubmitted safely.",
                    hint="Submit the generation again.",
                ),
            )
            return
        context = self.connections.context_for(connection)
        try:
            status = adapter.poll(context, submitted)
        except ApiError as error:
            self._handle_poll_error(job, error)
            return
        if status.state == "completed":
            if stored.get("cancel_requested_at"):
                self._cancel(job, message="The provider finished before the cancel reached it.")
                return
            self._transition(
                job,
                "downloading",
                status.message or "The provider finished; collecting the output.",
            )
            self._store_outputs(job, list(status.outputs))
            return
        if status.state == "failed":
            self._fail(
                job,
                ApiError(
                    status.error_code or "provider_error",
                    status.message or "The provider reported this generation as failed.",
                    details={"provider_status": status.provider_status or ""},
                ),
            )
            return
        if status.state == "cancelled":
            self._cancel(job, message=status.message or "The provider reports this job cancelled.")
            return
        # Still working. This is an observation, not a state change, so it is
        # recorded as an event rather than as a transition to the state the job
        # is already in.
        self._note(
            job,
            status.message or "The provider is still working on this job.",
            detail={
                "provider_status": status.provider_status or "",
                "queue_position": status.queue_position,
                "progress": status.progress,
            },
        )
        # Hand the lease back, due at the moment the provider asked to be asked
        # again. Holding it until it expired meant a two-minute lease silently
        # became a two-minute wait between polls, so a job that the provider
        # finished in seconds sat in "processing" for minutes. The delay is the
        # provider's, or the poll interval when it did not state one.
        from ..util import iso_in

        self.repos.jobs.release_lease(
            str(job.get("id") or ""),
            next_attempt_at=iso_in(status.retry_after_seconds or self.config.jobs.poll_interval_seconds),
        )
    # -- recording --------------------------------------------------------------

    def _store_outputs(self, job: Dict[str, Any], outputs: List[ProviderOutput]) -> None:
        """Import every output, then mark the job complete.

        A job is completed only if at least one artefact really reached object
        storage. Any output that could not be imported is recorded against its
        batch index with the reason, so the user sees a partial result with an
        explanation rather than a silent gap.
        """
        job_id = str(job.get("id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        user_id = str(job.get("created_by_user_id") or "")
        project_id = job.get("project_id")
        if not outputs:
            self._fail(
                job,
                ApiError(
                    "invalid_response",
                    "The provider reported this generation complete but returned no output this platform could read.",
                    hint="Check the model and mode against the provider documentation.",
                ),
            )
            return
        stored = 0
        problems: List[str] = []
        for index, output in enumerate(outputs):
            try:
                asset = self.assets.ingest_output(
                    workspace_id=workspace_id,
                    owner_user_id=user_id,
                    output=output,
                    project_id=project_id,
                    job_id=job_id,
                )
            except ApiError as error:
                problems.append("%s: %s" % (error.code, error.message))
                self.repos.jobs.add_output(
                    job_id,
                    workspace_id,
                    index,
                    provider_url=getattr(output, "url", "") or "",
                    ingest_error=error.message,
                )
                continue
            self.repos.jobs.add_output(
                job_id,
                workspace_id,
                index,
                asset_id=str(asset.get("id") or ""),
                provider_url=getattr(output, "url", "") or "",
            )
            stored += 1
        if stored == 0:
            self._fail(
                job,
                ApiError(
                    "output_download_failed",
                    "The provider produced an output, but none of it could be stored.",
                    details={"problems": problems[:5]},
                ),
            )
            return
        message = "%d output%s stored." % (stored, "" if stored == 1 else "s")
        if problems:
            message += " %d could not be stored." % len(problems)
        self._transition(
            job, "completed", message, detail={"stored": stored, "problems": problems[:5]}
        )
        self.stats.completed += 1

    def _fail(self, job: Dict[str, Any], error: ApiError) -> None:
        """Record a failure, retrying only when the provider said to.

        A retryable failure goes back to queued with a backoff while attempts
        remain; anything else stops here with the diagnostic attached. A job is
        never retried automatically when the provider may already have billed it.
        """
        job_id = str(job.get("id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        attempt = int(job.get("attempt") or 1)
        max_attempts = int(job.get("max_attempts") or self.config.jobs.max_attempts)
        retryable = error.code in RETRYABLE_CODES and attempt < max_attempts
        detail = scrub_structure(
            {
                "code": error.code,
                "retryable": retryable,
                "attempt": attempt,
                "max_attempts": max_attempts,
            }
        )
        if retryable:
            delay = min(300.0, float(self.config.jobs.retry_backoff_seconds) * (2 ** (attempt - 1)))
            from ..util import iso_in

            self.repos.jobs.release_lease(
                job_id,
                state="queued",
                message=error.message,
                next_attempt_at=iso_in(delay),
            )
            LOG.warning(
                "job will be retried",
                extra={"job_id": job_id, "code": error.code, "attempt": attempt, "delay": delay},
            )
            return
        self.repos.jobs.transition(
            workspace_id,
            job_id,
            state="failed",
            message=error.message,
            error={
                "code": error.code,
                "message": error.message,
                "hint": error.hint or "",
                "details": error.details,
                "provider_message": error.provider_message or "",
            },
            detail=detail,
        )
        self.stats.failed += 1
        LOG.warning("job failed", extra={"job_id": job_id, "code": error.code})
    def _cancel(self, job: Dict[str, Any], *, message: str = "") -> None:
        """Mark a job cancelled, and ask the provider to stop when it can.

        The provider call is attempted first: recording a cancellation we did not
        request would leave a billable job running with nothing watching it.
        """
        job_id = str(job.get("id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        note = message
        provider_ref = str(job.get("provider_job_ref") or "")
        if provider_ref:
            try:
                connection = self._connection(job)
                adapter = get_adapter(str(connection.get("adapter_type") or ""))
                context = self.connections.context_for(connection)
                live = adapter.cancel(
                    context,
                    SubmittedJob(
                        provider_job_id=provider_ref,
                        status_url=self._route(job, "status_url"),
                        result_url=self._route(job, "result_url"),
                    ),
                )
                note = live.message or note
            except ApiError as error:
                note = "The provider did not confirm the cancellation: %s" % error.message
        self.repos.jobs.transition(
            workspace_id,
            job_id,
            state="cancelled",
            message=note or "This job was cancelled.",
        )
        self.stats.cancelled += 1

    # -- helpers ----------------------------------------------------------------

    def _note(
        self,
        job: Dict[str, Any],
        message: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record an observation without changing the job's state.

        A poll that reports "still processing" is not a state change, and writing
        it as one would either be refused by the queue or, worse, append an event
        claiming a move that never happened. This appends the fact only.
        """
        self.repos.jobs.annotate(
            str(job.get("workspace_id") or ""),
            str(job.get("id") or ""),
            message=message,
            detail=detail,
        )

    def _transition(
        self,
        job: Dict[str, Any],
        state: str,
        message: str = "",
        *,
        detail: Optional[Dict[str, Any]] = None,
        provider_job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Advance the job and keep the caller copy in step.

        A move the queue refuses is not a reason to abort the whole pass: the job
        reached a state that already says what this call wanted to say (another
        worker finished it, or a cancel landed first). The refusal is logged with
        both states and the job is re-read, so the loop continues from the truth.
        """
        try:
            updated = self.repos.jobs.transition(
                str(job.get("workspace_id") or ""),
                str(job.get("id") or ""),
                state=state,
                message=message,
                detail=detail,
                provider_job_id=provider_job_id,
            )
        except ApiError as error:
            if error.code != "conflict":
                raise
            LOG.info(
                "job state move refused",
                extra={
                    "job_id": job.get("id"),
                    "wanted": state,
                    "reason": error.message,
                },
            )
            current = self.repos.jobs.get(
                str(job.get("workspace_id") or ""), str(job.get("id") or "")
            )
            job.update(current)
            return current
        job.update(updated)
        return updated

    def _remember_routes(self, job: Dict[str, Any], submitted: SubmittedJob) -> None:
        """Store the status and result routes so a restart does not lose them."""
        raw = job.get("request_json") or "{}"
        snapshot = json_loads(raw, {}) if isinstance(raw, str) else dict(raw or {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["routes"] = {
            "status_url": submitted.status_url or "",
            "result_url": submitted.result_url or "",
            "model_key": getattr(submitted, "model_key", ""),
        }
        text = json_dumps(snapshot)
        self.repos.jobs.update_routes(
            str(job.get("workspace_id") or ""), str(job.get("id") or ""), text
        )
        job["request_json"] = text

    def _route(self, job: Dict[str, Any], name: str) -> str:
        """One stored route, or an empty string when none was recorded."""
        raw = job.get("request_json") or "{}"
        snapshot = json_loads(raw, {}) if isinstance(raw, str) else dict(raw or {})
        if not isinstance(snapshot, dict):
            return ""
        routes = snapshot.get("routes") or {}
        if not isinstance(routes, dict):
            return ""
        return str(routes.get(name) or "")

    def _connection(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """The connection this job belongs to, or a clear refusal."""
        connection_id = str(job.get("connection_id") or "")
        if not connection_id:
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "This job is no longer attached to a provider connection, so it cannot be resumed.",
                details={"job_id": job.get("id")},
                hint="The connection was deleted. Submit the generation again with another connection.",
            )
        return self.repos.connections.get(
            str(job.get("workspace_id") or ""), connection_id
        )

    def _profile(self, job: Dict[str, Any]) -> Any:
        """The capability profile this job was created against."""
        from ..adapters.types import CapabilityProfile

        snapshot = json_loads(job.get("request_json") or "{}", {})
        if isinstance(snapshot, dict) and isinstance(snapshot.get("profile"), dict):
            return CapabilityProfile.from_public(snapshot["profile"])
        model_id = str(job.get("model_id") or "")
        if not model_id:
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "This job does not record which model it used, so its capabilities cannot be checked.",
            )
        model = self.repos.models.get(str(job.get("workspace_id") or ""), model_id)
        capabilities = model.get("capabilities") or {}
        return CapabilityProfile.from_public(
            dict(
                capabilities,
                modality=model.get("modality"),
                modes=tuple(model.get("modes") or ()),
            )
        )

    def _build_request(self, job: Dict[str, Any]) -> GenerationRequest:
        """Rebuild the exact request from the stored snapshot.

        The snapshot is what makes history reproducible: editing a model later
        does not change what this job sends.
        """
        snapshot = json_loads(job.get("request_json") or "{}", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        references = job.get("references") or []
        if isinstance(references, str):
            references = json_loads(references, [])
        parameters = job.get("parameters") or {}
        if isinstance(parameters, str):
            parameters = json_loads(parameters, {})
        return GenerationRequest(
            model_key=str(job.get("model_key") or snapshot.get("model_key") or ""),
            mode=str(job.get("mode") or "text_to_image"),
            prompt=str(job.get("prompt") or ""),
            negative_prompt=str(job.get("negative_prompt") or "") or None,
            parameters=dict(parameters or {}),
            references=tuple(references or ()),
            mask_url=snapshot.get("mask_url") or None,
            prompt_instructions=tuple(snapshot.get("prompt_instructions") or ()),
            seed=job.get("seed"),
            count=int(job.get("count") or 1),
        )

    def _record_usage(
        self,
        job: Dict[str, Any],
        connection: Dict[str, Any],
        request: GenerationRequest,
        *,
        provider_ref: str = "",
    ) -> None:
        """Write one usage row, billed to the user own provider.

        No amount is recorded unless a provider reported one, because an invented
        cost in a usage table becomes an invented invoice.
        """
        try:
            self.repos.usage.record(
                str(job.get("workspace_id") or ""),
                {
                    "user_id": job.get("created_by_user_id"),
                    "job_id": job.get("id"),
                    "connection_id": connection.get("id"),
                    "model_id": job.get("model_id"),
                    "billing_party": "user_provider",
                    "modality": str(job.get("modality") or "image"),
                    "units_json": json_dumps(
                        {"outputs": request.count, "provider_reference": provider_ref}
                    ),
                    "estimated_cost_json": json_dumps(job.get("cost_estimate") or {}),
                },
            )
        except Exception:  # noqa: BLE001 - usage bookkeeping must not fail a job
            LOG.exception("usage could not be recorded", extra={"job_id": job.get("id")})

    def _handle_submit_error(self, job: Dict[str, Any], error: ApiError) -> None:
        """A failed submit is retried only when the provider said it was retryable.

        A rejection of the key, the model id or the request itself is terminal:
        retrying it would spend the user rate limit to learn the same thing.
        """
        self._fail(job, error)

    def _handle_poll_error(self, job: Dict[str, Any], error: ApiError) -> None:
        """A failed poll never resubmits; it retries the poll or gives up."""
        if error.code == TIMEOUT:
            self.repos.jobs.release_lease(
                str(job.get("id") or ""),
                state="processing",
                message="The provider did not answer the status request in time.",
            )
            return
        self._fail(job, error)
