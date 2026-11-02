"""Executing a canvas: one ordinary generation job per node, in graph order.

Why a run is polled rather than driven in the request
-----------------------------------------------------
A chain of three video generations takes minutes. Holding the HTTP request open
for it would be the wrong shape for a serverless deployment, and a background
thread started by a request can be frozen the moment the response is written.
So a run advances the same way a job does: the client polls, and the poll does one
useful step. The first call queues every node whose inputs already exist; each
later call reads the jobs that were queued and, when their outputs exist, queues
the nodes that were waiting on them.

The property that matters
-------------------------
A node is queued only when every node that feeds it has a stored output. A
downstream generation never receives a URL that does not exist, and a failed
upstream node stops the branch instead of letting it run with a missing source.
Nothing here invents a status: a node is ``done`` when its job is ``completed``
and the job has an output asset.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..canvas import plan
from ..errors import ApiError

#: Node states a run uses. Deliberately the same words the queue uses, plus
#: "pending" for a node that has not been queued yet.
_PENDING = "pending"
_QUEUED = "queued"
_DONE = "done"
_FAILED = "failed"
_SKIPPED = "skipped"

#: The queue states in which a job can still be driven.
_ACTIVE = ("queued", "validating", "submitting", "processing", "downloading")


def _terminal(status: str) -> bool:
    return status in ("completed", "failed", "cancelled", "timed_out")


class CanvasRunService:
    """Plan a canvas, queue what is ready, and report only what happened."""

    def __init__(self, repositories: Any, generations: Any, runner: Any = None) -> None:
        self.repos = repositories
        self.generations = generations
        self.runner = runner

    # -- starting ------------------------------------------------------------

    def start(self, session: Any, canvas_id: str) -> Dict[str, Any]:
        """Open a run and queue the nodes that have no unmet dependency."""
        session.require("generation:create")
        canvas = self.repos.canvases.get(session.workspace_id, canvas_id)
        steps = plan(canvas.get("nodes") or [], canvas.get("edges") or [])
        order = [str(step["node_id"]) for step in steps]
        if not order:
            raise ApiError(
                "validation_failed",
                "This canvas has no generate node, so a run would do nothing.",
                field="nodes",
            )
        run = self.repos.canvases.create_run(
            session.workspace_id, canvas_id, session.user_id, order
        )
        states = {node_id: {"status": _PENDING, "job_id": "", "asset_id": "", "error": ""} for node_id in order}
        self.repos.canvases.update_run(
            session.workspace_id, run["id"], status="running", node_states=states, started=True
        )
        return self.advance(session, canvas_id, run["id"], steps=steps)

    # -- advancing -----------------------------------------------------------

    def advance(
        self,
        session: Any,
        canvas_id: str,
        run_id: str,
        *,
        steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Move a run as far as the provider's answers allow.

        Idempotent: a node that already has a job is never queued again, so a
        client polling every two seconds cannot buy the same frame twice.
        """
        session.require("generation:read")
        canvas = self.repos.canvases.get(session.workspace_id, canvas_id)
        if steps is None:
            steps = plan(canvas.get("nodes") or [], canvas.get("edges") or [])
        run = self.repos.canvases.get_run(session.workspace_id, canvas_id, run_id)
        if run["status"] in ("completed", "failed", "cancelled"):
            return self._shape(run, steps)

        states: Dict[str, Dict[str, Any]] = dict(run.get("node_states") or {})
        for step in steps:
            states.setdefault(
                str(step["node_id"]),
                {"status": _PENDING, "job_id": "", "asset_id": "", "error": ""},
            )

        failure = ""
        for step in steps:
            node_id = str(step["node_id"])
            state = states[node_id]

            # A node whose upstream is not done cannot be queued yet. If an
            # upstream failed, this node will never run and is marked skipped so
            # the run's report does not pretend it is still waiting.
            upstream_states = [
                states.get(str(step.get("source_from") or ""), None),
                *[states.get(str(item["from"]), None) for item in step.get("references_from") or []],
            ]
            upstream_states = [item for item in upstream_states if item is not None]
            if any(item["status"] == _FAILED or item["status"] == _SKIPPED for item in upstream_states):
                if state["status"] == _PENDING:
                    state["status"] = _SKIPPED
                    state["error"] = "An upstream node did not produce an output."
                continue
            blocked = any(item["status"] != _DONE for item in upstream_states)

            if state["status"] in (_QUEUED, "validating", "submitting", "processing", "downloading"):
                job = self.repos.jobs.get(session.workspace_id, str(state["job_id"]))
                # The read advances the queued job the same way the job route
                # does: claim this job, drive one step, then read the truth back.
                # Without this a run polled by a client would only ever observe
                # a job nobody was moving.
                if self.runner is not None and str(job.get("status") or "") in _ACTIVE:
                    claimed = self.repos.jobs.claim_job(
                        str(job.get("id") or ""), "canvas-" + str(job.get("id") or "")[:8],
                        lease_seconds=60,
                    )
                    if claimed is not None:
                        self.runner.drive(claimed)
                    job = self.repos.jobs.get(session.workspace_id, str(state["job_id"]))
                status = str(job.get("status") or "")
                if status == "completed":
                    asset_id = next(
                        (
                            str(output.get("asset_id") or "")
                            for output in job.get("outputs") or ()
                            if output.get("asset_id")
                        ),
                        "",
                    )
                    if asset_id:
                        state.update({"status": _DONE, "asset_id": asset_id, "error": ""})
                    else:
                        state.update(
                            {"status": _FAILED, "error": "The job completed without a stored output."}
                        )
                        failure = failure or "Node '%s' completed without an output." % node_id
                elif status in ("failed", "cancelled", "timed_out"):
                    state.update(
                        {
                            "status": _FAILED,
                            "error": str(job.get("error_message") or "The provider did not complete this node."),
                        }
                    )
                    failure = failure or "Node '%s' failed: %s" % (
                        node_id,
                        state["error"],
                    )
                continue

            if state["status"] == _PENDING and not blocked:
                try:
                    job = self._queue(session, step, states)
                except ApiError as error:
                    state.update({"status": _FAILED, "error": error.message})
                    failure = failure or "Node '%s' was refused: %s" % (node_id, error.message)
                    continue
                state.update({"status": _QUEUED, "job_id": str(job.get("id") or ""), "error": ""})

        # A node may have finished in this pass; a downstream node that was
        # blocked at the top of the loop is queued on the next pass. Re-read the
        # states once so the reported status is the one just written.
        statuses = [states[str(step["node_id"])]["status"] for step in steps]
        if failure:
            run = self.repos.canvases.update_run(
                session.workspace_id, run_id, status="failed", node_states=states, error=failure, finished=True
            )
        elif all(item == _DONE for item in statuses):
            run = self.repos.canvases.update_run(
                session.workspace_id, run_id, status="completed", node_states=states, finished=True
            )
        else:
            run = self.repos.canvases.update_run(
                session.workspace_id, run_id, status="running", node_states=states
            )
        return self._shape(run, steps)

    # -- queueing one node ---------------------------------------------------

    def _queue(
        self,
        session: Any,
        step: Mapping[str, Any],
        states: Mapping[str, Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """Turn one planned node into a generation request.

        The model's own capabilities still decide whether the request is accepted;
        a canvas does not bypass a single check. The mode defaults to the
        text-to-X mode, or to the image-to-X mode when a source asset is attached,
        because that is the only honest inference from the graph.
        """
        model_id = str(step["model_id"])
        model = self.repos.models.get(session.workspace_id, model_id)
        modality = str(model.get("modality") or "image")
        if modality not in ("image", "video"):
            raise ApiError(
                "validation_failed",
                "Node '%s' names a model that is not an image or video model." % step["node_id"],
                field="model_id",
            )
        references: List[Dict[str, str]] = []
        source_id = str(step.get("source_from") or "")
        if source_id:
            source_state = states.get(source_id) or {}
            if not source_state.get("asset_id"):
                raise ApiError(
                    "validation_failed",
                    "Node '%s' needs the output of '%s', which is not stored yet."
                    % (step["node_id"], source_id),
                )
            asset = self.repos.assets.get(session.workspace_id, str(source_state["asset_id"]))
            references.append(
                {
                    "asset_id": str(asset["id"]),
                    "role": "first_frame" if modality == "video" else "main_subject",
                }
            )
        for item in step.get("references_from") or []:
            from_state = states.get(str(item["from"])) or {}
            if not from_state.get("asset_id"):
                raise ApiError(
                    "validation_failed",
                    "Node '%s' references '%s', which has no stored output."
                    % (step["node_id"], item["from"]),
                )
            references.append(
                {"asset_id": str(from_state["asset_id"]), "role": str(item["role"])}
            )

        mode = str(step.get("mode") or "").strip()
        if not mode:
            if modality == "image":
                mode = "image_to_image" if source_id else "text_to_image"
            else:
                mode = "image_to_video" if source_id else "text_to_video"
        payload: Dict[str, Any] = {
            "model_id": model_id,
            "mode": mode,
            "prompt": step["prompt"],
            "negative_prompt": step.get("negative_prompt") or "",
            "parameters": dict(step.get("parameters") or {}),
            "count": int(step.get("count") or 1),
            "references": references,
            "client_request_id": "canvas:%s:%s" % (step["node_id"], run_nonce()),
        }
        result = self.generations.queue(session, payload, modality=modality, mode=mode)
        return result["job"]

    # -- shaping -------------------------------------------------------------

    def _shape(self, run: Mapping[str, Any], steps: List[Mapping[str, Any]]) -> Dict[str, Any]:
        """Add the plan's labels and the next node to run, for the screen."""
        states = dict(run.get("node_states") or {})
        order = [str(step["node_id"]) for step in steps]
        next_node = next((node_id for node_id in order if states.get(node_id, {}).get("status") == _PENDING), "")
        return dict(run, next_node=next_node, node_order=order)


def run_nonce() -> str:
    """A fresh suffix so the same node in two runs is two real generations."""
    import uuid

    return uuid.uuid4().hex
