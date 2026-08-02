"""Aggregator: merge, dedup, agreement, and honest reporting of failed lanes."""

from __future__ import annotations

from prreview.aggregator import aggregate, dedupe, sort_findings
from prreview.contracts import AgentResult, AgentType, Finding, Severity


def _f(
    agent: AgentType = AgentType.SECURITY,
    severity: Severity = Severity.MAJOR,
    path: str = "a.py",
    line: int = 10,
    confidence: float = 0.5,
    category: str = "c",
) -> Finding:
    return Finding(
        agent_type=agent, severity=severity, category=category, summary="s",
        file_path=path, line_start=line, line_end=line,
        confidence=confidence, rationale="r",
    )


class TestDedupe:
    def test_distinct_locations_are_all_kept(self) -> None:
        out = dedupe([_f(line=10), _f(line=20), _f(path="b.py", line=10)])
        assert len(out) == 3

    def test_same_conclusion_from_two_lanes_collapses_and_counts_agreement(self) -> None:
        out = dedupe([
            _f(agent=AgentType.SECURITY, confidence=0.9),
            _f(agent=AgentType.QUALITY, confidence=0.4),
        ])
        assert len(out) == 1
        assert out[0].agreement == 2
        assert out[0].confidence == 0.9, "the higher-confidence finding should win"

    def test_three_lanes_agreeing_counts_three(self) -> None:
        out = dedupe([
            _f(agent=AgentType.SECURITY), _f(agent=AgentType.QUALITY), _f(agent=AgentType.TESTS)
        ])
        assert out[0].agreement == 3

    def test_one_lane_reporting_two_issues_on_one_line_keeps_both(self) -> None:
        """B1 regression, the loss half.

        Keying on location alone collapsed two *different* issues that happened
        to share a line — routine real-model behaviour — and the loser's
        category, summary and rationale were unrecoverable.
        """
        out = dedupe([
            _f(category="sql-injection", severity=Severity.CRITICAL, confidence=0.92),
            _f(category="missing-input-validation", severity=Severity.MAJOR, confidence=0.70),
        ])
        assert len(out) == 2, "a lane's second issue on the same line was dropped"
        assert {f.category for f in out} == {"sql-injection", "missing-input-validation"}

    def test_a_lane_cannot_corroborate_itself(self) -> None:
        """B1 regression, the fabrication half.

        `agreement` counted findings rather than distinct lanes, so a single
        specialist produced a finding claiming two lanes agreed. On a project
        whose named failure mode is rubber-stamping an almost-right review, an
        inflated corroboration badge is the worst field to inflate.
        """
        out = dedupe([
            _f(agent=AgentType.SECURITY, category="sql-injection", confidence=0.9),
            _f(agent=AgentType.SECURITY, category="sql-injection", confidence=0.5),
        ])
        assert len(out) == 1
        assert out[0].agreement == 1, "one lane reported twice; that is not agreement"

    def test_agreement_counts_distinct_lanes_not_findings(self) -> None:
        out = dedupe([
            _f(agent=AgentType.SECURITY, confidence=0.9),
            _f(agent=AgentType.SECURITY, confidence=0.8),
            _f(agent=AgentType.QUALITY, confidence=0.7),
        ])
        assert len(out) == 1
        assert out[0].agreement == 2, "3 findings, but only 2 distinct lanes"

    def test_different_conclusions_from_different_lanes_both_survive(self) -> None:
        out = dedupe([
            _f(agent=AgentType.SECURITY, category="sql-injection"),
            _f(agent=AgentType.QUALITY, category="resource-leak"),
        ])
        assert len(out) == 2, "two different conclusions are two findings"

    def test_dedup_cannot_downgrade_severity(self) -> None:
        """The judgment call that matters.

        If security says CRITICAL and quality says MINOR about the same line, the
        merged finding must surface CRITICAL — even when the MINOR one has higher
        confidence and therefore wins the merge. Deduplication must never be able
        to hide a risk.
        """
        out = dedupe([
            _f(agent=AgentType.SECURITY, severity=Severity.CRITICAL, confidence=0.4),
            _f(agent=AgentType.QUALITY, severity=Severity.MINOR, confidence=0.95),
        ])
        assert len(out) == 1
        assert out[0].severity is Severity.CRITICAL
        assert out[0].confidence == 0.95

    def test_dedup_is_order_independent_for_severity(self) -> None:
        a = dedupe([_f(severity=Severity.CRITICAL, confidence=0.4),
                    _f(severity=Severity.MINOR, confidence=0.9)])
        b = dedupe([_f(severity=Severity.MINOR, confidence=0.9),
                    _f(severity=Severity.CRITICAL, confidence=0.4)])
        assert a[0].severity is b[0].severity is Severity.CRITICAL

    def test_same_line_different_file_is_not_a_duplicate(self) -> None:
        assert len(dedupe([_f(path="a.py", line=7), _f(path="b.py", line=7)])) == 2


class TestSort:
    def test_worst_severity_first_then_confidence(self) -> None:
        out = sort_findings([
            _f(severity=Severity.INFO, confidence=0.99, line=1),
            _f(severity=Severity.CRITICAL, confidence=0.3, line=2),
            _f(severity=Severity.CRITICAL, confidence=0.8, line=3),
        ])
        assert [f.severity for f in out][0] is Severity.CRITICAL
        assert out[0].confidence == 0.8, "most confident critical first"
        assert out[-1].severity is Severity.INFO


class TestAggregate:
    def test_reports_every_lane_that_ran(self) -> None:
        results = [AgentResult(agent_type=a) for a in AgentType]
        review = aggregate("r1", results)
        assert len(review.agents_run) == 4
        assert review.agents_failed == ()
        assert review.incomplete is False

    def test_a_failed_lane_makes_the_review_incomplete(self) -> None:
        """The partial-review guarantee: a crashed lane is never a clean lane."""
        results = [
            AgentResult(agent_type=AgentType.SECURITY, findings=(_f(),)),
            AgentResult(agent_type=AgentType.QUALITY, ok=False, error="boom"),
            AgentResult(agent_type=AgentType.TESTS),
            AgentResult(agent_type=AgentType.DOCS),
        ]
        review = aggregate("r2", results)
        assert review.incomplete is True
        assert review.agents_failed == (AgentType.QUALITY,)
        assert len(review.agents_run) == 4, "a failed lane still counts as run"
        assert len(review.findings) == 1, "the surviving lane's findings are kept"

    def test_findings_from_a_failed_lane_are_not_used(self) -> None:
        results = [
            AgentResult(agent_type=AgentType.SECURITY, ok=False, error="boom", findings=(_f(),)),
        ]
        assert aggregate("r3", results).findings == ()

    def test_dropped_counts_are_summed_across_lanes(self) -> None:
        results = [
            AgentResult(agent_type=AgentType.SECURITY, dropped_ungrounded=2),
            AgentResult(agent_type=AgentType.QUALITY, dropped_ungrounded=3),
        ]
        assert aggregate("r4", results).dropped_ungrounded == 5

    def test_max_severity_reflects_the_worst_finding(self) -> None:
        results = [AgentResult(agent_type=AgentType.SECURITY, findings=(
            _f(severity=Severity.MINOR, line=1), _f(severity=Severity.CRITICAL, line=2),
        ))]
        assert aggregate("r5", results).max_severity is Severity.CRITICAL

    def test_empty_review_has_no_max_severity(self) -> None:
        assert aggregate("r6", [AgentResult(agent_type=AgentType.DOCS)]).max_severity is None
