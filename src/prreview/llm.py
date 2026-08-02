"""LLM client boundary.

Adapter layer. Every network call in this project goes through here or through
`github.py` — INV-2 checks that nothing else calls out directly, because an
unbounded wait inside a CI job is this system's most likely real-world failure
and it stays invisible until the day it happens.

Two implementations:
  * `OfflineLLM`  — deterministic, no network. Powers `--offline`, and every test.
  * `AnthropicLLM` — the real one, with an explicit timeout and bounded retries.

The offline client is not a mock bolted on for tests; it is what makes the M1
demo command runnable in CI with no credentials at all.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

DEFAULT_TIMEOUT_S: float = 60.0
DEFAULT_MAX_RETRIES: int = 2


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens: int


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or returns unusable output."""


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> LLMResponse: ...


# ─────────────────────────────── offline ────────────────────────────────────


# Heuristics used only by the offline client to produce plausible, *grounded*
# findings. They are intentionally crude: the offline client exists to prove the
# pipeline, contracts and gates work end to end, not to review code well.
_OFFLINE_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, category, severity, why)
    (r"""(?i)\b(?:execute|executemany)\s*\(\s*(?:f["']|["'].*%s)""",
     "sql-injection", "critical",
     "String-interpolated SQL reaches the driver; a hostile value changes the query shape."),
    (r"""(?i)\b(?:eval|exec)\s*\(""",
     "arbitrary-code-execution", "critical",
     "eval/exec on runtime data executes whatever the caller can influence."),
    (r"""(?i)(?:password|secret|token|api_key)\s*=\s*["'][^"']+["']""",
     "hardcoded-secret", "critical",
     "A literal credential in source is a credential in git history forever."),
    (r"""(?i)\b(?:requests|httpx)\.(?:get|post|put|delete)\s*\((?![^)]*timeout)""",
     "missing-timeout", "major",
     "An outbound call with no timeout can hang the whole job indefinitely."),
    (r"""(?i)except\s*:\s*$|except\s+Exception\s*:\s*(?:#.*)?$""",
     "broad-except", "minor",
     "A bare except swallows the failure that would have told you what broke."),
    (r"""(?i)\bprint\s*\(""",
     "print-instead-of-logging", "info",
     "print() bypasses log levels and structured output."),
    (r"""(?i)#\s*(?:TODO|FIXME|XXX)\b""",
     "unresolved-marker", "minor",
     "A TODO shipped in a diff is a decision deferred past the point of review."),
]

# Which offline categories belong to which specialist, so the four lanes differ.
_AGENT_CATEGORIES: dict[str, set[str]] = {
    "security": {"sql-injection", "arbitrary-code-execution", "hardcoded-secret"},
    "quality": {"broad-except", "print-instead-of-logging", "missing-timeout"},
    "tests": {"unresolved-marker"},
    "docs": {"unresolved-marker"},
}


class OfflineLLM:
    """Deterministic stand-in. Same input → same output, always.

    Emits the JSON envelope the real model is asked for, so the parsing and
    validation path under test is the same one production uses.
    """

    def __init__(self, agent_type: str = "security") -> None:
        self.agent_type = agent_type

    def complete(self, system: str, user: str) -> LLMResponse:
        wanted = _AGENT_CATEGORIES.get(self.agent_type, set())
        findings: list[dict[str, object]] = []
        current_file = ""

        for raw in user.splitlines():
            if raw.startswith("--- FILE: "):
                current_file = raw[len("--- FILE: "):].strip()
                continue
            m = re.match(r"^(\d+):\s?(.*)$", raw)
            if not m or not current_file:
                continue
            line_no, code = int(m.group(1)), m.group(2)

            for pattern, category, severity, why in _OFFLINE_PATTERNS:
                if category not in wanted:
                    continue
                if re.search(pattern, code):
                    findings.append({
                        "severity": severity,
                        "category": category,
                        "summary": f"{category.replace('-', ' ')} at {current_file}:{line_no}",
                        "file_path": current_file,
                        "line_start": line_no,
                        "line_end": line_no,
                        "suggestion": _SUGGESTIONS.get(category, ""),
                        "confidence": _CONFIDENCE.get(category, 0.6),
                        "rationale": why,
                    })
                    break

        payload = json.dumps({"findings": findings})
        # Rough token proxy — enough for the events spine to show non-zero cost.
        return LLMResponse(text=payload, tokens=(len(system) + len(user)) // 4)


_SUGGESTIONS: dict[str, str] = {
    "sql-injection": "Use parameterised queries: cursor.execute(sql, (value,)).",
    "arbitrary-code-execution": "Replace eval/exec with an explicit dispatch table.",
    "hardcoded-secret": "Read the value from the environment or a secret store.",
    "missing-timeout": "Pass an explicit timeout= to the call.",
    "broad-except": "Catch the specific exception you can actually handle.",
    "print-instead-of-logging": "Use the logging module so level and destination are controllable.",
    "unresolved-marker": "Resolve it, or link it to a tracked issue before merge.",
}

_CONFIDENCE: dict[str, float] = {
    "sql-injection": 0.92,
    "arbitrary-code-execution": 0.90,
    "hardcoded-secret": 0.88,
    "missing-timeout": 0.75,
    "broad-except": 0.65,
    "print-instead-of-logging": 0.55,
    "unresolved-marker": 0.60,
}


class FailingLLM:
    """Always raises. Used to prove a dead lane degrades instead of crashing."""

    def __init__(self, message: str = "simulated model failure") -> None:
        self.message = message

    def complete(self, system: str, user: str) -> LLMResponse:
        raise LLMError(self.message)


# ─────────────────────────────── live ───────────────────────────────────────


class AnthropicLLM:
    """The real client. Explicit timeout, bounded retries, no unbounded waits."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s  # INV-2: always set, never None
        self.max_retries = max_retries
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def complete(self, system: str, user: str) -> LLMResponse:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError(
                "anthropic SDK not installed; use --offline or `pip install '.[llm]'`"
            ) from exc

        if not self._api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set; use --offline to run without a key")

        client = anthropic.Anthropic(api_key=self._api_key, timeout=self.timeout_s)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    timeout=self.timeout_s,
                )
                text = "".join(
                    block.text for block in msg.content if getattr(block, "type", "") == "text"
                )
                tokens = int(msg.usage.input_tokens + msg.usage.output_tokens)
                return LLMResponse(text=text, tokens=tokens)
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised as LLMError
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(2**attempt)  # bounded backoff: 1s, 2s
                    continue
        raise LLMError(f"LLM call failed after {self.max_retries + 1} attempts: {last_error}")
