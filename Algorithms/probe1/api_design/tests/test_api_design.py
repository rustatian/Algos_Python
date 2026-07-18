"""Tests for the long-running-request API ladder.

The work is an injected handler and the worker is an explicit process_*
step, so every test is deterministic — no threads, no sleeping. Tier 1 is
synchronous; Tier 2 is the submit->poll lifecycle; Tier 3 adds request_id
idempotency.
"""

from api_design import (
    AsyncJobAPI,
    IdempotentJobAPI,
    JobStatus,
    SyncAPI,
)


# ----------------------------------------------------------------------
# Tier 1 — SyncAPI.
# ----------------------------------------------------------------------


def test_sync_returns_result_inline() -> None:
    api = SyncAPI(handler=lambda p: p * 2)
    assert api.submit(21) == 42


# ----------------------------------------------------------------------
# Tier 2 — AsyncJobAPI: submit -> poll.
# ----------------------------------------------------------------------


def test_submit_returns_pending_immediately() -> None:
    api = AsyncJobAPI(handler=lambda p: p.upper())
    rec = api.submit("hello")
    assert rec.status is JobStatus.PENDING
    assert rec.result is None  # not processed yet


def test_job_runs_only_after_worker_processes() -> None:
    api = AsyncJobAPI(handler=lambda p: p.upper())
    rec = api.submit("hello")
    # Before the worker runs, polling still shows PENDING.
    assert api.get(rec.id).status is JobStatus.PENDING
    api.process_all()
    done = api.get(rec.id)
    assert done.status is JobStatus.SUCCESS
    assert done.result == "HELLO"


def test_failed_job_records_error_not_raises() -> None:
    def boom(_p: object) -> None:
        raise ValueError("kaboom")

    api = AsyncJobAPI(handler=boom)
    rec = api.submit("x")
    api.process_all()  # must NOT raise — the worker captures the error
    done = api.get(rec.id)
    assert done.status is JobStatus.FAILED
    assert "kaboom" in (done.error or "")


def test_get_unknown_job_returns_none() -> None:
    api = AsyncJobAPI(handler=lambda p: p)
    assert api.get("does-not-exist") is None


def test_process_next_reports_whether_it_worked() -> None:
    api = AsyncJobAPI(handler=lambda p: p)
    api.submit("a")
    assert api.process_next() is True  # one job processed
    assert api.process_next() is False  # queue now empty


def test_jobs_processed_in_fifo_order() -> None:
    order: list[str] = []
    api = AsyncJobAPI(handler=lambda p: order.append(p))
    api.submit("first")
    api.submit("second")
    api.process_all()
    assert order == ["first", "second"]


def test_distinct_submits_get_distinct_ids() -> None:
    api = AsyncJobAPI(handler=lambda p: p)
    a = api.submit("x")
    b = api.submit("x")
    assert a.id != b.id  # no idempotency at Tier 2 — two jobs


# ----------------------------------------------------------------------
# Tier 3 — IdempotentJobAPI: request_id dedup.
# ----------------------------------------------------------------------


def test_same_request_id_returns_same_job() -> None:
    api = IdempotentJobAPI(handler=lambda p: p.upper())
    a = api.submit("x", request_id="r1")
    b = api.submit("x", request_id="r1")  # a retry of the same request
    assert a.id == b.id  # one job, not two


def test_idempotent_submit_runs_handler_once() -> None:
    calls: list[str] = []
    api = IdempotentJobAPI(handler=lambda p: calls.append(p))
    api.submit("payload", request_id="r1")
    api.submit("payload", request_id="r1")  # duplicate
    api.submit("payload", request_id="r1")  # duplicate
    api.process_all()
    assert calls == ["payload"]  # handler invoked exactly once


def test_different_request_ids_create_distinct_jobs() -> None:
    api = IdempotentJobAPI(handler=lambda p: p)
    a = api.submit("x", request_id="r1")
    b = api.submit("x", request_id="r2")
    assert a.id != b.id


def test_idempotent_get_and_poll_lifecycle() -> None:
    api = IdempotentJobAPI(handler=lambda p: p + "!")
    rec = api.submit("hi", request_id="r1")
    assert api.get(rec.id).status is JobStatus.PENDING
    api.process_all()
    assert api.get(rec.id).result == "hi!"


def test_retry_after_completion_still_returns_same_job() -> None:
    """A duplicate submit arriving AFTER the job finished returns the
    finished job (with its result), never a fresh PENDING one.
    """
    api = IdempotentJobAPI(handler=lambda p: p.upper())
    first = api.submit("x", request_id="r1")
    api.process_all()
    again = api.submit("x", request_id="r1")
    assert again.id == first.id
    assert again.status is JobStatus.SUCCESS
    assert again.result == "X"
