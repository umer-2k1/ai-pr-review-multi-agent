"""INV-3: no ungrounded finding.

The context-graph names this file as the invariant's check command. A finding
that cites a file or line absent from the diff must be dropped before it ever
reaches a human — one hallucinated location discredits every other finding in
the review.
"""

from __future__ import annotations

import json
from pathlib import Path

from prreview.agents.security import SecuritySpecialist
from prreview.diff import parse_diff
from prreview.llm import LLMResponse, OfflineLLM

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.diff"


class HallucinatingLLM:
    """Returns findings pointing at places that do not exist in the diff."""

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "findings": [
                    {  # real file, line far outside every hunk
                        "severity": "critical", "category": "invented",
                        "summary": "s", "file_path": "app/db.py",
                        "line_start": 9999, "line_end": 9999,
                        "suggestion": "", "confidence": 0.99, "rationale": "r",
                    },
                    {  # file that is not in the diff at all
                        "severity": "major", "category": "invented",
                        "summary": "s", "file_path": "app/not_in_diff.py",
                        "line_start": 1, "line_end": 1,
                        "suggestion": "", "confidence": 0.99, "rationale": "r",
                    },
                    {  # this one IS grounded and must survive
                        "severity": "major", "category": "real",
                        "summary": "s", "file_path": "app/db.py",
                        "line_start": 10, "line_end": 10,
                        "suggestion": "", "confidence": 0.7, "rationale": "r",
                    },
                ]
            }),
            tokens=10,
        )


def test_ungrounded_findings_are_dropped() -> None:
    diff = parse_diff(FIXTURE.read_text())
    result = SecuritySpecialist(HallucinatingLLM()).review(diff)

    assert result.ok is True
    assert len(result.findings) == 1, "only the grounded finding may survive"
    assert result.findings[0].category == "real"


def test_every_offline_finding_is_grounded() -> None:
    """The real path, not a contrived one: whatever the offline client produces
    must land inside a hunk of the file it names."""
    diff = parse_diff(FIXTURE.read_text())
    result = SecuritySpecialist(OfflineLLM("security")).review(diff)

    assert result.findings, "fixture should produce at least one security finding"
    for f in result.findings:
        assert diff.is_grounded(f.file_path, f.line_start, f.line_end), (
            f"ungrounded finding leaked through: {f.file_path}:{f.line_start}"
        )


def test_ungrounded_count_is_reportable() -> None:
    diff = parse_diff(FIXTURE.read_text())
    spec = SecuritySpecialist(HallucinatingLLM())
    text = HallucinatingLLM().complete("", "").text
    assert spec.ungrounded_count(diff, text) == 2


def test_dropped_findings_are_counted_on_the_result() -> None:
    """B5 regression: dropping must be visible, not silent."""
    diff = parse_diff(FIXTURE.read_text())
    result = SecuritySpecialist(HallucinatingLLM()).review(diff)
    assert result.dropped_ungrounded == 2
    assert len(result.findings) == 1


class TotallyHallucinatingLLM:
    """Every finding cites a file that is not in the diff."""

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "findings": [{
                    "severity": "critical", "category": "invented",
                    "summary": "s", "file_path": "nowhere/at/all.py",
                    "line_start": 1, "line_end": 1,
                    "suggestion": "", "confidence": 0.99, "rationale": "r",
                }]
            }),
            tokens=10,
        )


def test_total_hallucination_is_distinguishable_from_a_clean_lane() -> None:
    """B5 regression, the one that matters.

    A lane whose every finding was hallucinated used to be byte-identical to a
    lane that genuinely found nothing: ok=True, findings=(), error=None. The
    reviewer would read "no findings" and move on.
    """
    diff = parse_diff(FIXTURE.read_text())
    hallucinated = SecuritySpecialist(TotallyHallucinatingLLM()).review(diff)
    clean = SecuritySpecialist(SilentLLM()).review(diff)

    assert hallucinated.findings == () and clean.findings == ()
    assert hallucinated.all_findings_were_hallucinated is True
    assert clean.all_findings_were_hallucinated is False
    assert hallucinated.dropped_ungrounded == 1
    assert clean.dropped_ungrounded == 0


class SilentLLM:
    """Genuinely found nothing — a valid review."""

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(text='{"findings": []}', tokens=10)
