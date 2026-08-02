"""The Finding contract — the thing every specialist returns.

Core layer: this module imports nothing else from `prreview` (INV-1). Everything
downstream merges *data*, not prose, and that is only possible because this shape
is fixed here.

`confidence` and `rationale` are required, not optional. The study's named primary
failure mode is the "almost-right" problem: reviews that are 90% correct while the
reader drifts into rubber-stamping. A finding that cannot say how sure it is, and
why, is exactly the finding that causes it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AgentType(str, Enum):
    """The four specialist concerns, derived from how a human reviews."""

    SECURITY = "security"
    QUALITY = "quality"
    TESTS = "tests"
    DOCS = "docs"


class Severity(str, Enum):
    """Ordered low→high. CRITICAL is load-bearing: it forces a HITL hold (M3)."""

    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.MINOR: 1,
    Severity.MAJOR: 2,
    Severity.CRITICAL: 3,
}


class Finding(BaseModel):
    """One structured observation about one location in the diff."""

    model_config = ConfigDict(frozen=True)

    agent_type: AgentType
    severity: Severity
    category: str = Field(min_length=1, description="Short slug, e.g. 'sql-injection'.")
    summary: str = Field(min_length=1, description="One sentence: what is wrong.")
    file_path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    suggestion: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, description="Why the agent believes this.")

    # Set by the aggregator when >1 specialist independently flags the same line.
    agreement: int = Field(default=1, ge=1)

    def location_key(self) -> tuple[str, int]:
        """Dedup key: same file + same starting line is the same location."""
        return (self.file_path, self.line_start)


class AgentResult(BaseModel):
    """One specialist's whole contribution, including how it failed if it did.

    A specialist that raises does not vanish — it returns a result with `ok=False`
    and an error string. The aggregator counts these, so a crashed lane can never
    be reported as a clean lane.
    """

    model_config = ConfigDict(frozen=True)

    agent_type: AgentType
    findings: tuple[Finding, ...] = ()
    ok: bool = True
    error: str | None = None
    tokens: int = 0
    duration_ms: int = 0

    # How many findings this lane produced that cited a location absent from the
    # diff, and were therefore dropped (INV-3). Counted, not just discarded: a
    # lane whose every output was hallucinated would otherwise be indistinguishable
    # from a genuinely clean lane, and a rising count is the earliest warning that
    # the model is drifting.
    dropped_ungrounded: int = Field(default=0, ge=0)

    @property
    def all_findings_were_hallucinated(self) -> bool:
        """True when the lane produced output, and none of it survived grounding."""
        return self.ok and not self.findings and self.dropped_ungrounded > 0


class HitlVerdict(str, Enum):
    """The publish gate. Level 2 autonomy: the agent drafts, a human approves."""

    DRAFT_READY = "DRAFT_READY"  # confident, no CRITICAL — still needs a human click
    HOLD = "HOLD"  # low confidence or CRITICAL present — must be read by a human


class Review(BaseModel):
    """The aggregated output of one review run."""

    model_config = ConfigDict(frozen=True)

    review_id: str
    findings: tuple[Finding, ...] = ()
    agents_run: tuple[AgentType, ...] = ()
    agents_failed: tuple[AgentType, ...] = ()
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    hitl_verdict: HitlVerdict = HitlVerdict.HOLD
    incomplete: bool = False
    dropped_ungrounded: int = 0

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: SEVERITY_RANK[f.severity]).severity
