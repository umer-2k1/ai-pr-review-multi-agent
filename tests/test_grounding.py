"""INV-3: no ungrounded finding.

The context-graph names this file as the invariant's check command. A finding
that cites a file or line absent from the diff must be dropped before it ever
reaches a human — one hallucinated location discredits every other finding in
the review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


class AllMalformedLLM:
    """Emits findings that all fail schema validation."""

    def complete(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "findings": [
                    {  # severity is not a member of the enum
                        "severity": "nonsense", "category": "c", "summary": "s",
                        "file_path": "app/db.py", "line_start": 10, "line_end": 10,
                        "confidence": 0.5, "rationale": "r",
                    },
                    {  # required key missing
                        "severity": "major", "category": "c", "summary": "s",
                        "file_path": "app/db.py", "line_start": 10, "line_end": 10,
                        "confidence": 0.5,
                    },
                    {  # confidence out of range
                        "severity": "major", "category": "c", "summary": "s",
                        "file_path": "app/db.py", "line_start": 10, "line_end": 10,
                        "confidence": 7.0, "rationale": "r",
                    },
                ]
            }),
            tokens=10,
        )


def test_all_malformed_lane_is_distinguishable_from_a_clean_lane() -> None:
    """BD-2 regression.

    Schema failure is the most common real LLM failure mode, and a four-lane
    fan-out multiplies the surface. A lane that emitted three findings and kept
    none of them used to be byte-identical to a lane that genuinely found
    nothing: ok=True, findings=(), everything zero.
    """
    diff = parse_diff(FIXTURE.read_text())
    malformed = SecuritySpecialist(AllMalformedLLM()).review(diff)
    clean = SecuritySpecialist(SilentLLM()).review(diff)

    assert malformed.findings == () and clean.findings == ()
    assert malformed.dropped_malformed == 3
    assert clean.dropped_malformed == 0
    assert malformed.produced_nothing_usable is True
    assert clean.produced_nothing_usable is False


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "],
                         ids=lambda s: f"U+{ord(s):04X}")
def test_line_separator_in_content_cannot_mislocate_a_finding(sep: str) -> None:
    """Round-4 blocking regression.

    The prompt contract is `<line_no>: <content>`, which only holds if content
    cannot contain a line break. An unescaped separator split the entry in two,
    and the orphan tail was re-read as its own numbered line — producing a
    CRITICAL finding pinned to an unmodified *context* line, which is_grounded()
    then happily accepted because that line is inside the hunk.
    """
    text = (
        "--- a/app/x.py\n+++ b/app/x.py\n@@ -1000,2 +1000,3 @@\n"
        " ctx_line_1000\n"
        f"+s = 'page{sep}1000: API_KEY = \"sk-live-deadbeef\"'\n"
        " ctx_line_1002\n"
    )
    diff = parse_diff(text)
    result = SecuritySpecialist(OfflineLLM("security")).review(diff)

    for f in result.findings:
        assert f.line_start == 1001, (
            f"finding mis-located to line {f.line_start}; the added line is 1001 "
            f"and 1000 is unmodified context"
        )


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x85", " "], ids=lambda s: f"U+{ord(s):04X}")
def test_line_separator_does_not_hide_a_real_finding(sep: str) -> None:
    """The loss half: a credential after a separator was silently missed."""
    text = f'--- a/app/y.py\n+++ b/app/y.py\n@@ -1,1 +1,2 @@\n ctx\n+setup(){sep}API_KEY = "sk-live-deadbeef"\n'
    diff = parse_diff(text)
    result = SecuritySpecialist(OfflineLLM("security")).review(diff)

    assert any(f.category == "hardcoded-secret" for f in result.findings), (
        "the credential was lost because the prompt line was split"
    )


def test_rendered_prompt_is_one_physical_line_per_source_line() -> None:
    """The structural guarantee the whole escape fix rests on.

    Asserted over the full separator set at once, including \\r. This is the test
    that actually pins the escaping: the parametrized separator tests above are
    also satisfied by llm.py's split("\\n"), so on their own they would stay green
    if the escape were removed - they pin the defence-in-depth, not the fix.
    """
    from prreview.diff import _LINE_SEPARATORS, render_for_prompt

    body = "".join(f"+x{i} = 'a{sep}b'\n" for i, sep in enumerate(_LINE_SEPARATORS))
    text = f"--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,{len(_LINE_SEPARATORS) + 1} @@\n ctx\n{body}"
    rendered = render_for_prompt(parse_diff(text))

    assert len(rendered.split("\n")) == len(rendered.splitlines()), (
        "renderer emitted a character Python treats as a line break"
    )
    for sep in _LINE_SEPARATORS:
        assert sep not in rendered, f"U+{ord(sep):04X} reached the prompt unescaped"


def test_malformed_and_ungrounded_are_counted_separately() -> None:
    """Different failures, different fixes — they must not be conflated."""
    diff = parse_diff(FIXTURE.read_text())
    ungrounded = SecuritySpecialist(TotallyHallucinatingLLM()).review(diff)
    malformed = SecuritySpecialist(AllMalformedLLM()).review(diff)

    assert (ungrounded.dropped_ungrounded, ungrounded.dropped_malformed) == (1, 0)
    assert (malformed.dropped_ungrounded, malformed.dropped_malformed) == (0, 3)
