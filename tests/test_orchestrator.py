"""Orchestrator: the parallel fan-out, and what happens when a lane misbehaves."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from prreview.contracts import AgentType
from prreview.diff import parse_diff
from prreview.llm import FailingLLM, LLMClient, LLMResponse, OfflineLLM
from prreview.orchestrator import SPECIALISTS, run_review

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.diff"
DUPLICATE = Path(__file__).parent.parent / "fixtures" / "duplicate.diff"


def _diff(path: Path = FIXTURE):  # type: ignore[no-untyped-def]
    with path.open("r", newline="") as fh:
        return parse_diff(fh.read())


def _offline(at: AgentType) -> LLMClient:
    return OfflineLLM(agent_type=at.value)


def test_all_four_specialists_run() -> None:
    review = run_review(_diff(), _offline)
    assert len(review.agents_run) == 4
    assert set(review.agents_run) == set(SPECIALISTS)
    assert review.incomplete is False


def test_lane_order_is_deterministic_regardless_of_completion_order() -> None:
    """Same diff must yield the same review every time, or golden tests are useless."""
    first = run_review(_diff(), _offline, review_id="fixed")
    second = run_review(_diff(), _offline, review_id="fixed")
    assert first.agents_run == second.agents_run
    assert [f.merge_key() for f in first.findings] == [f.merge_key() for f in second.findings]


def test_lanes_actually_run_in_parallel() -> None:
    """Four lanes that each sleep must finish in well under four sleeps."""
    barrier_hits: list[float] = []
    lock = threading.Lock()

    class SlowLLM:
        def complete(self, system: str, user: str) -> LLMResponse:
            with lock:
                barrier_hits.append(time.monotonic())
            time.sleep(0.20)
            return LLMResponse(text='{"findings": []}', tokens=1)

    started = time.monotonic()
    run_review(_diff(), lambda at: SlowLLM())
    elapsed = time.monotonic() - started

    assert len(barrier_hits) == 4
    assert elapsed < 0.60, f"looks sequential: {elapsed:.2f}s for 4x0.20s lanes"


def test_one_failing_lane_yields_a_flagged_partial_review() -> None:
    """M2's contractual behaviour: a raising specialist must not crash the run,
    and must not be reported as having found nothing."""

    def factory(at: AgentType) -> LLMClient:
        return FailingLLM() if at is AgentType.QUALITY else OfflineLLM(at.value)

    review = run_review(_diff(), factory)

    assert review.incomplete is True
    assert review.agents_failed == (AgentType.QUALITY,)
    assert len(review.agents_run) == 4
    assert review.findings, "the three healthy lanes still produced findings"


def test_every_lane_failing_still_returns_a_review() -> None:
    review = run_review(_diff(), lambda at: FailingLLM())
    assert review.incomplete is True
    assert len(review.agents_failed) == 4
    assert review.findings == ()


def test_a_hung_lane_is_reported_not_waited_on() -> None:
    """The orchestration-deadlock failure mode: one stuck lane must not hold the
    whole review hostage."""

    class HangingLLM:
        def complete(self, system: str, user: str) -> LLMResponse:
            time.sleep(30)
            return LLMResponse(text='{"findings": []}', tokens=1)

    def factory(at: AgentType) -> LLMClient:
        return HangingLLM() if at is AgentType.DOCS else OfflineLLM(at.value)

    started = time.monotonic()
    review = run_review(_diff(), factory, timeout_s=0.5)
    elapsed = time.monotonic() - started

    # Scaled to the deadline, not a loose absolute: a bound of 5s against a
    # 0.5s timeout tolerates a 10x regression silently.
    assert elapsed < 0.5 * 4, f"orchestrator blocked for {elapsed:.2f}s against a 0.5s deadline"
    assert AgentType.DOCS in review.agents_failed
    assert review.incomplete is True


def test_duplicate_finding_appears_once_with_agreement() -> None:
    """M2's success criterion 2, end to end.

    `fixtures/duplicate.diff` plants a `# TODO` line, which the tests and docs
    lanes both flag as `unresolved-marker`. It must appear ONCE with
    agreement == 2.

    An earlier version of this test asserted only that locations were unique —
    which passed even with `dedupe` replaced by the identity function, because
    the fixture contained no collision at all. It proved nothing.
    """
    review = run_review(_diff(DUPLICATE), _offline)

    markers = [f for f in review.findings if f.category == "unresolved-marker"]
    assert len(markers) == 1, f"the TODO should merge to one finding, got {len(markers)}"
    assert markers[0].agreement == 2, (
        f"tests and docs both flag it; agreement should be 2, got {markers[0].agreement}"
    )

    keys = [f.merge_key() for f in review.findings]
    assert len(keys) == len(set(keys)), f"duplicate keys survived: {keys}"


class SilentLLM:
    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text='{"findings": []}', tokens=1)


class TwoIssuesOnOneLineLLM:
    """One lane, two different conclusions about the SAME line.

    `OfflineLLM` cannot produce this — it `break`s after the first pattern match
    per line — which is precisely why the offline path never exposed B1.
    """

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text=json.dumps({"findings": [
            {"severity": "critical", "category": "sql-injection", "summary": "injection",
             "file_path": "app/db.py", "line_start": 10, "line_end": 10,
             "suggestion": "", "confidence": 0.92, "rationale": "interpolated SQL"},
            {"severity": "major", "category": "missing-input-validation", "summary": "no validation",
             "file_path": "app/db.py", "line_start": 10, "line_end": 10,
             "suggestion": "", "confidence": 0.70, "rationale": "username is unchecked"},
        ]}), tokens=1)


def test_a_single_lane_never_reports_agreement_above_one() -> None:
    """B1 regression, end to end.

    An earlier version of this test used OfflineLLM, whose two findings land on
    *different* lines — so nothing ever collided and the test passed with the
    full original B1 bug restored. It asserted nothing. This stub forces a real
    same-line, same-lane collision, which is the only shape that reproduces B1.
    """

    def only_security(at: AgentType) -> LLMClient:
        return TwoIssuesOnOneLineLLM() if at is AgentType.SECURITY else SilentLLM()

    review = run_review(_diff(), only_security)

    assert len(review.findings) == 2, (
        f"one lane's two issues on one line collapsed to {len(review.findings)}"
    )
    assert {f.category for f in review.findings} == {"sql-injection", "missing-input-validation"}
    for f in review.findings:
        assert f.agreement == 1, (
            f"{f.category} claims {f.agreement} lanes agree, but only security ran"
        )


class ExplodingFactoryError(RuntimeError):
    pass


def test_a_raising_client_factory_becomes_four_failed_lanes() -> None:
    """A factory that blows up must not escape as a traceback: the caller is
    promised a Review, and 'could not build the clients' is a reportable failure."""

    def boom(at: AgentType) -> LLMClient:
        raise ExplodingFactoryError("no credentials")

    review = run_review(_diff(), boom)

    assert len(review.agents_run) == 4
    assert len(review.agents_failed) == 4
    assert review.incomplete is True
    assert review.findings == ()


def test_on_event_seam_fires_for_every_stage() -> None:
    """M4's events spine plugs in here; the seam must already be exercised."""
    seen: list[tuple[str, str | None]] = []

    def sink(event_type, agent_type, result):  # type: ignore[no-untyped-def]
        seen.append((event_type, agent_type.value if agent_type else None))

    run_review(_diff(), _offline, on_event=sink)

    kinds = [e for e, _ in seen]
    assert kinds[0] == "review_started"
    assert kinds[-1] == "review_completed"
    assert kinds.count("agent_completed") == 4


def test_review_id_is_stable_when_supplied_and_unique_when_not() -> None:
    assert run_review(_diff(), _offline, review_id="abc").review_id == "abc"
    a = run_review(_diff(), _offline).review_id
    b = run_review(_diff(), _offline).review_id
    assert a != b and len(a) == 12
