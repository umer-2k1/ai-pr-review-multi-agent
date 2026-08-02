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
                        "line_start": 15, "line_end": 15,
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
