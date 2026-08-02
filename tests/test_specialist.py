"""Specialist behaviour: parsing, failure handling, and the contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from prreview.agents.security import SecuritySpecialist
from prreview.contracts import AgentType, Severity
from prreview.diff import parse_diff
from prreview.llm import FailingLLM, LLMResponse, OfflineLLM

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.diff"


def _diff():  # type: ignore[no-untyped-def]
    return parse_diff(FIXTURE.read_text())


def test_security_specialist_finds_the_planted_issues() -> None:
    result = SecuritySpecialist(OfflineLLM("security")).review(_diff())
    assert result.ok is True
    assert result.agent_type is AgentType.SECURITY
    categories = {f.category for f in result.findings}
    assert "sql-injection" in categories
    assert "hardcoded-secret" in categories


def test_security_lane_ignores_other_concerns() -> None:
    """The fixture also contains a print() and a TODO. They belong to other
    lanes; duplicating them here would waste the reviewer's attention."""
    result = SecuritySpecialist(OfflineLLM("security")).review(_diff())
    categories = {f.category for f in result.findings}
    assert "print-instead-of-logging" not in categories
    assert "unresolved-marker" not in categories


def test_a_failing_model_degrades_instead_of_raising() -> None:
    result = SecuritySpecialist(FailingLLM()).review(_diff())
    assert result.ok is False
    assert result.error is not None
    assert "simulated model failure" in result.error
    assert result.findings == ()


class GarbageLLM:
    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text="I'm afraid I can't do that.", tokens=5)


def test_non_json_output_is_a_lane_failure_not_a_crash() -> None:
    result = SecuritySpecialist(GarbageLLM()).review(_diff())
    assert result.ok is False
    assert result.findings == ()


class PartiallyMalformedLLM:
    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text='{"findings": ['
            '{"severity": "nonsense-level", "category": "c", "summary": "s",'
            ' "file_path": "app/db.py", "line_start": 15, "line_end": 15,'
            ' "confidence": 0.5, "rationale": "r"},'
            '{"severity": "major", "category": "good", "summary": "s",'
            ' "file_path": "app/db.py", "line_start": 15, "line_end": 15,'
            ' "confidence": 0.5, "rationale": "r"}]}',
            tokens=5,
        )


def test_one_malformed_finding_does_not_sink_the_lane() -> None:
    result = SecuritySpecialist(PartiallyMalformedLLM()).review(_diff())
    assert result.ok is True
    assert len(result.findings) == 1
    assert result.findings[0].category == "good"


def test_findings_are_immutable() -> None:
    result = SecuritySpecialist(OfflineLLM("security")).review(_diff())
    with pytest.raises(Exception):
        result.findings[0].confidence = 0.1  # type: ignore[misc]


def test_confidence_is_bounded() -> None:
    result = SecuritySpecialist(OfflineLLM("security")).review(_diff())
    for f in result.findings:
        assert 0.0 <= f.confidence <= 1.0
        assert f.rationale, "rationale is required, not decorative"


def test_severity_ordering_is_usable() -> None:
    from prreview.contracts import SEVERITY_RANK

    assert SEVERITY_RANK[Severity.CRITICAL] > SEVERITY_RANK[Severity.MAJOR]
    assert SEVERITY_RANK[Severity.MAJOR] > SEVERITY_RANK[Severity.MINOR]
    assert SEVERITY_RANK[Severity.MINOR] > SEVERITY_RANK[Severity.INFO]
