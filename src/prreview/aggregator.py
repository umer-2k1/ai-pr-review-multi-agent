"""Merge the specialist lanes into one review.

Domain layer. Imports `contracts` only.

This is the step that only works because `Finding` is structured: the aggregator
merges *data*, not prose. Dedup by location, agreement counting and (in M3) a
confidence threshold are all impossible against four blobs of English — which is
the concrete reason `PLAN.md`'s brainstorm chose the fan-out over one big prompt.

M2 scope: merge, deduplicate, order, and report which lanes failed.
M3 adds `overall_confidence` and the HITL gate on top of what is here.
"""

from __future__ import annotations

from prreview.contracts import (
    SEVERITY_RANK,
    AgentResult,
    AgentType,
    Finding,
    Review,
)


def _worst(*severities: object) -> object:
    return max(severities, key=lambda s: SEVERITY_RANK[s])  # type: ignore[index]


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that several specialists raised at the same location.

    Keeps the highest-confidence one and records how many lanes agreed.

    Agreement is signal, not noise: two independent specialists landing on the
    same line is the strongest evidence this pipeline can produce, so it has to
    survive the merge rather than be discarded as a duplicate.

    Severity is taken as the **worst** across the agreeing lanes, not the
    winner's. If security says CRITICAL and quality says MINOR about the same
    line, the review must surface CRITICAL — deduplication must never be able to
    downgrade a risk, because the whole point of the security lane is that its
    misses are the expensive ones.
    """
    best: dict[tuple[str, int], Finding] = {}
    order: list[tuple[str, int]] = []

    for f in findings:
        key = f.location_key()
        current = best.get(key)
        if current is None:
            best[key] = f
            order.append(key)
            continue
        winner = f if f.confidence > current.confidence else current
        best[key] = winner.model_copy(update={
            "agreement": current.agreement + 1,
            "severity": _worst(winner.severity, current.severity, f.severity),
        })

    return [best[k] for k in order]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Worst first, then most confident. Reviewer attention is the scarce resource."""
    return sorted(
        findings,
        key=lambda f: (-SEVERITY_RANK[f.severity], -f.confidence, f.file_path, f.line_start),
    )


def aggregate(review_id: str, results: list[AgentResult]) -> Review:
    """Merge every lane's output into a single Review.

    A failed lane contributes no findings but is still named in `agents_failed`
    and sets `incomplete`. That is the difference between "we reviewed this and
    found nothing" and "we did not finish reviewing this" — collapsing the two
    is how a crashed specialist gets read as a clean bill of health.
    """
    ok = [r for r in results if r.ok]
    failed = [r.agent_type for r in results if not r.ok]

    merged = sort_findings(dedupe([f for r in ok for f in r.findings]))

    return Review(
        review_id=review_id,
        findings=tuple(merged),
        agents_run=tuple(r.agent_type for r in results),
        agents_failed=tuple(failed),
        incomplete=bool(failed),
        dropped_ungrounded=sum(r.dropped_ungrounded for r in results),
    )


def lanes_reporting_nothing_usable(results: list[AgentResult]) -> list[AgentType]:
    """Lanes that emitted findings and kept none — a malfunction, not a clean lane."""
    return [r.agent_type for r in results if r.produced_nothing_usable]
