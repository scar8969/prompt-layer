"""The client translates the server's job status into the screen's job state.

Why this test exists
--------------------
The server stores a job's progress in a `status` column whose values are fixed by
a CHECK constraint: queued, validating, submitting, processing, downloading,
completed, failed, cancelled, timed_out. The screens speak a different word for
the last one — `succeeded` — and the whole client, from the poller to the submit
button, is written against it.

The client read `state` off the payload. The payload carried `status`. So every
job looked queued: the poller never stopped, the submit button never came back,
and a finished generation had no way to say it had finished. Nothing in the
server suite could see it, because the server was right.

The mapping now lives in one place in `web/src/api/endpoints.ts`. This test reads
both files and fails when the server grows a status the client has not
translated — the failure mode that made the bug possible.

Run:  python -m pytest tests/test_job_vocabulary.py -q
"""

from __future__ import annotations

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(REPO, "studio", "migrations", "0004_jobs.sql")
ENDPOINTS = os.path.join(REPO, "web", "src", "api", "endpoints.ts")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def server_statuses() -> set:
    """Every status the database will store, read from its CHECK constraint."""
    text = _read(MIGRATION)
    match = re.search(
        r"status\s+TEXT NOT NULL\s*\n\s*CHECK \(status IN \(([^)]*)\)\)",
        text,
    )
    assert match, "the jobs status CHECK constraint moved; update this test with it"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def client_translations() -> dict:
    """The client's status -> state table."""
    text = _read(ENDPOINTS)
    block = re.search(r"const JOB_STATE_FROM_STATUS[^{]*\{(.*?)\};", text, re.S)
    assert block, "JOB_STATE_FROM_STATUS is gone from endpoints.ts"
    return dict(re.findall(r"^\s*([a-z_]+):\s*'([a-z_]+)'", block.group(1), re.M))


def test_every_server_status_has_a_client_translation():
    missing = sorted(server_statuses() - set(client_translations()))
    assert missing == [], (
        "the server can store these statuses and the client does not translate "
        "them, so a job in one of them would look queued forever: %r" % (missing,)
    )


def test_the_client_does_not_translate_a_status_the_server_never_stores():
    extra = sorted(set(client_translations()) - server_statuses())
    assert extra == [], "the client translates statuses the database cannot store: %r" % (extra,)


def test_a_finished_job_reads_as_succeeded_because_that_is_what_the_screens_check():
    """Every terminal check in the client is written against `succeeded`."""
    translations = client_translations()
    assert translations["completed"] == "succeeded"
    assert translations["failed"] == "failed"
    assert translations["cancelled"] == "cancelled"


def test_the_poller_stops_on_every_terminal_status():
    """A status the client maps to a non-terminal state would poll forever."""
    terminal_states = {"succeeded", "failed", "cancelled", "canceled"}
    finished = {"completed", "failed", "cancelled", "timed_out"}
    translations = client_translations()
    for status in finished:
        assert translations[status] in terminal_states, (
            "%s is a finished status but maps to %r, which the poller does not "
            "treat as terminal" % (status, translations[status])
        )
