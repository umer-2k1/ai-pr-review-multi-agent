"""Shared specialist shape.

Domain layer. Imports `contracts`, `diff` and `llm` only — never `orchestrator`,
`cli`, `github` or `events` (INV-1). That restriction is what keeps every
specialist runnable offline, with no network and no filesystem, which in turn is
what makes the milestone demo commands runnable in CI.

Each specialist differs only in its domain prompt and its `agent_type`. The
grounding check, the parsing, the error handling and the token accounting all
live here, once.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod

from pydantic import ValidationError

from prreview.contracts import AgentResult, AgentType, Finding, Severity
from prreview.diff import Diff, render_for_prompt
from prreview.llm import LLMClient, LLMError

_SYSTEM_PREAMBLE = """You are one specialist in a multi-agent pull-request review.
You review ONLY your own concern. Another agent covers the others; do not duplicate their work.

Return STRICT JSON, no prose, no markdown fence:
{"findings": [{"severity": "info|minor|major|critical", "category": "<short-slug>",
"summary": "<one sentence>", "file_path": "<path exactly as shown>",
"line_start": <int>, "line_end": <int>, "suggestion": "<concrete fix>",
"confidence": <0.0-1.0>, "rationale": "<why you believe this>"}]}

Rules:
- Cite ONLY file paths and line numbers shown in the diff. A citation you cannot see is a defect.
- If you find nothing in your concern, return {"findings": []}. Saying nothing is a valid review.
- confidence is your genuine belief, not a formality. Low confidence is useful; false certainty is not.
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class Specialist(ABC):
    """One grounded reasoner over a diff."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    @property
    @abstractmethod
    def agent_type(self) -> AgentType: ...

    @property
    @abstractmethod
    def concern(self) -> str:
        """The domain prompt — the only thing that really differs between lanes."""

    def review(self, diff: Diff) -> AgentResult:
        """Run this specialist. Never raises: a failure becomes ok=False.

        The whole point is that a dead lane is *reported*, not hidden. An
        exception escaping here would let the orchestrator mistake a crash for a
        clean review, which is the one outcome the design forbids.
        """
        started = time.monotonic()
        try:
            rendered = render_for_prompt(diff)
            response = self.client.complete(
                system=_SYSTEM_PREAMBLE + "\n\nYOUR CONCERN:\n" + self.concern,
                user=rendered,
            )
            raw = self._parse(response.text)
            grounded = tuple(f for f in raw if diff.is_grounded(f.file_path, f.line_start, f.line_end))
            return AgentResult(
                agent_type=self.agent_type,
                findings=grounded,
                ok=True,
                tokens=response.tokens,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except (LLMError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            return AgentResult(
                agent_type=self.agent_type,
                findings=(),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    def ungrounded_count(self, diff: Diff, text: str) -> int:
        """How many findings the model produced that cite locations not in the diff.

        Surfaced so the number can be reported rather than silently swallowed —
        a rising count is the early warning that the model is drifting.
        """
        raw = self._parse(text)
        return sum(1 for f in raw if not diff.is_grounded(f.file_path, f.line_start, f.line_end))

    def _parse(self, text: str) -> list[Finding]:
        """Extract Findings from the model's JSON envelope.

        Tolerant of a stray markdown fence or leading prose, strict about the
        shape once found: a malformed finding is dropped, not guessed at.
        """
        match = _JSON_RE.search(text)
        if not match:
            raise ValueError("no JSON object in model output")
        payload = json.loads(match.group(0))
        items = payload.get("findings", [])
        if not isinstance(items, list):
            raise ValueError("'findings' is not a list")

        out: list[Finding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                out.append(
                    Finding(
                        agent_type=self.agent_type,
                        severity=Severity(str(item["severity"]).lower()),
                        category=str(item["category"]),
                        summary=str(item["summary"]),
                        file_path=str(item["file_path"]),
                        line_start=int(item["line_start"]),
                        line_end=int(item["line_end"]),
                        suggestion=str(item.get("suggestion", "")),
                        confidence=float(item["confidence"]),
                        rationale=str(item["rationale"]),
                    )
                )
            except (KeyError, ValueError, TypeError, ValidationError):
                continue  # one malformed finding must not sink the lane
        return out
