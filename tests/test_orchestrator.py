"""Orchestrator: the parallel fan-out, and what happens when a lane misbehaves."""

from __future__ import annotations

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
    assert [f.location_key() for f in first.findings] == [f.location_key() for f in second.findings]


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

    assert elapsed < 5, f"orchestrator blocked for {elapsed:.1f}s on a hung lane"
    assert AgentType.DOCS in review.agents_failed
    assert review.incomplete is True


def test_duplicate_finding_appears_once_with_agreement() -> None:
    """The fixture plants a line that both security and quality flag."""
    review = run_review(_diff(DUPLICATE), _offline)
    keys = [f.location_key() for f in review.findings]
    assert len(keys) == len(set(keys)), f"duplicate locations survived: {keys}"


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
