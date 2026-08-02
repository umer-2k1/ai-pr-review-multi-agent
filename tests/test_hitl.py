"""The human-in-the-loop gate: when does a person have to read this?

The autonomy level chosen at G0 is the study's level 2 — the agent drafts, a
human approves. These tests pin the asymmetry that makes the gate safe: HOLD
wins every ambiguous case, because a needless HOLD costs two minutes and a
wrongly-released draft costs the credibility of every review after it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prreview.aggregator import aggregate
from prreview.contracts import AgentResult, AgentType, Finding, HitlVerdict, Severity
from prreview.diff import parse_diff
from prreview.hitl import (
    CONFIDENCE_THRESHOLD,
    compute_confidence,
    decide,
    render_draft,
)
from prreview.llm import LLMClient, OfflineLLM
from prreview.orchestrator import run_review

CRITICAL_FIXTURE = Path(__file__).parent.parent / "fixtures" / "critical.diff"


def _f(severity: Severity = Severity.MINOR, confidence: float = 0.9, line: int = 1) -> Finding:
    return Finding(
        agent_type=AgentType.SECURITY, severity=severity, category="c", summary="s",
        file_path="a.py", line_start=line, line_end=line,
        confidence=confidence, rationale="r",
    )


class TestConfidence:
    def test_mean_of_findings(self) -> None:
        assert compute_confidence([_f(confidence=0.8), _f(confidence=0.6, line=2)], 0) == 0.7

    def test_empty_but_complete_review_is_not_certainty(self) -> None:
        """'We looked and found nothing' is a real result, but not a certainty.

        Reporting 1.0 for silence teaches the reader to stop checking."""
        score = compute_confidence([], 0)
        assert 0.0 < score < 1.0

    def test_a_failed_lane_lowers_confidence(self) -> None:
        """A lane that did not finish is missing evidence, not evidence of absence."""
        assert compute_confidence([_f(confidence=0.9)], 1) < compute_confidence([_f(confidence=0.9)], 0)

    def test_more_failed_lanes_lower_it_further(self) -> None:
        scores = [compute_confidence([_f(confidence=0.9)], n) for n in range(5)]
        assert scores == sorted(scores, reverse=True)

    def test_confidence_stays_in_range(self) -> None:
        for failed in range(0, 10):
            score = compute_confidence([_f(confidence=1.0)], failed)
            assert 0.0 <= score <= 1.0

    def test_one_confident_finding_cannot_rescue_many_shaky_ones(self) -> None:
        """Mean, not max: a single strong finding must not make five weak ones
        look trustworthy."""
        shaky = [_f(confidence=0.2, line=i) for i in range(1, 6)]
        assert compute_confidence([*shaky, _f(confidence=1.0, line=9)], 0) < CONFIDENCE_THRESHOLD


class TestGate:
    def test_critical_forces_hold_regardless_of_confidence(self) -> None:
        """M3's central criterion.

        Not a threshold decision: the consequence of being wrong about a missed
        injection is categorically different from being wrong about a style nit.
        """
        verdict, reason = decide([_f(severity=Severity.CRITICAL, confidence=1.0)], failed_lanes=0)
        assert verdict is HitlVerdict.HOLD
        assert "CRITICAL" in reason

    @pytest.mark.parametrize("confidence", [0.0, 0.5, 0.75, 0.99, 1.0])
    def test_critical_holds_at_every_confidence(self, confidence: float) -> None:
        verdict, _ = decide([_f(severity=Severity.CRITICAL, confidence=confidence)], failed_lanes=0)
        assert verdict is HitlVerdict.HOLD

    def test_a_failed_lane_forces_hold(self) -> None:
        verdict, reason = decide([_f(confidence=1.0)], failed_lanes=1)
        assert verdict is HitlVerdict.HOLD
        assert "did not finish" in reason

    def test_a_lane_that_kept_nothing_forces_hold(self) -> None:
        verdict, reason = decide([_f(confidence=1.0)], failed_lanes=0, lanes_nothing_usable=1)
        assert verdict is HitlVerdict.HOLD
        assert "malfunction" in reason

    def test_low_confidence_forces_hold(self) -> None:
        verdict, reason = decide([_f(confidence=0.1)], failed_lanes=0)
        assert verdict is HitlVerdict.HOLD
        assert "threshold" in reason

    def test_high_confidence_no_critical_all_lanes_is_draft_ready(self) -> None:
        verdict, reason = decide([_f(severity=Severity.MAJOR, confidence=0.95)], failed_lanes=0)
        assert verdict is HitlVerdict.DRAFT_READY
        assert reason

    def test_every_verdict_carries_a_reason(self) -> None:
        """A gate that holds without saying why trains people to click through it."""
        cases = [
            ([_f(severity=Severity.CRITICAL)], 0, 0),
            ([_f(confidence=0.95)], 1, 0),
            ([_f(confidence=0.95)], 0, 1),
            ([_f(confidence=0.1)], 0, 0),
            ([_f(confidence=0.95)], 0, 0),
        ]
        for findings, failed, nothing_usable in cases:
            _, reason = decide(findings, failed_lanes=failed, lanes_nothing_usable=nothing_usable)
            assert reason and len(reason) > 10

    def test_critical_outranks_every_other_reason(self) -> None:
        """When several conditions fire at once, the reader should be told the
        most serious one."""
        _, reason = decide(
            [_f(severity=Severity.CRITICAL, confidence=0.1)],
            failed_lanes=2, lanes_nothing_usable=1,
        )
        assert "CRITICAL" in reason


class TestDraft:
    def test_draft_is_produced_even_on_hold(self) -> None:
        """The point of the gate is that a human READS the draft; withholding it
        on HOLD would defeat it."""
        md = render_draft("r1", [_f(severity=Severity.CRITICAL)], HitlVerdict.HOLD, "because")
        assert "HOLD" in md
        assert "because" in md
        assert "a.py:1" in md

    def test_draft_says_a_human_must_approve(self) -> None:
        md = render_draft("r1", [_f()], HitlVerdict.DRAFT_READY, "fine")
        assert "human" in md.lower() and "approve" in md.lower()

    def test_empty_draft_is_still_honest(self) -> None:
        md = render_draft("r1", [], HitlVerdict.DRAFT_READY, "nothing found")
        assert "No findings" in md

    def test_agreement_is_surfaced_in_the_draft(self) -> None:
        f = _f().model_copy(update={"agreement": 3})
        assert "3 lanes agree" in render_draft("r1", [f], HitlVerdict.HOLD, "x")


class TestEndToEnd:
    def test_critical_fixture_holds_via_the_orchestrator(self) -> None:
        with CRITICAL_FIXTURE.open("r", newline="") as fh:
            diff = parse_diff(fh.read())

        def offline(at: AgentType) -> LLMClient:
            return OfflineLLM(at.value)

        review = run_review(diff, offline)
        assert review.max_severity is Severity.CRITICAL
        assert review.hitl_verdict is HitlVerdict.HOLD
        assert "CRITICAL" in review.hitl_reason

    def test_aggregate_populates_the_gate_fields_with_the_computed_value(self) -> None:
        """Asserts the actual number, not merely `> 0`.

        The weaker assertion survived a mutation that hardcoded
        `overall_confidence=1.0` — which would present a shaky review as certain.
        """
        review = aggregate("r", [AgentResult(agent_type=AgentType.SECURITY, findings=(
            _f(confidence=0.90, line=1), _f(confidence=0.80, line=2),
        ))])
        assert review.overall_confidence == pytest.approx(0.85)
        assert review.hitl_reason != ""

    def test_a_lane_that_kept_nothing_is_wired_through_to_the_gate(self) -> None:
        """The wiring, not just the branch.

        `decide()`'s nothing-usable branch was tested in isolation, but nothing
        pinned that `AgentResult.produced_nothing_usable` actually reaches it —
        the mutation `lanes_nothing_usable=0` survived the whole suite.
        """
        results = [
            AgentResult(agent_type=AgentType.SECURITY, findings=(_f(confidence=0.99),)),
            # emitted findings, kept none of them: a malfunction, not a clean lane
            AgentResult(agent_type=AgentType.QUALITY, ok=True, dropped_ungrounded=3),
        ]
        review = aggregate("r", results)
        assert review.hitl_verdict is HitlVerdict.HOLD
        assert "kept none" in review.hitl_reason

    def test_malformed_only_lane_also_reaches_the_gate(self) -> None:
        results = [
            AgentResult(agent_type=AgentType.SECURITY, findings=(_f(confidence=0.99),)),
            AgentResult(agent_type=AgentType.DOCS, ok=True, dropped_malformed=2),
        ]
        review = aggregate("r", results)
        assert review.hitl_verdict is HitlVerdict.HOLD
        assert "kept none" in review.hitl_reason

    def test_review_defaults_to_hold_when_the_gate_is_not_set(self) -> None:
        """The safe default is load-bearing: anything constructing a Review
        without running the gate must not claim it is ready to post."""
        from prreview.contracts import Review

        bare = Review(review_id="x")
        assert bare.hitl_verdict is HitlVerdict.HOLD
        assert bare.overall_confidence == 0.0

    def test_incomplete_review_never_reaches_draft_ready(self) -> None:
        results = [
            AgentResult(agent_type=AgentType.SECURITY, findings=(_f(confidence=0.99),)),
            AgentResult(agent_type=AgentType.QUALITY, ok=False, error="boom"),
        ]
        review = aggregate("r", results)
        assert review.incomplete is True
        assert review.hitl_verdict is HitlVerdict.HOLD
