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
    Severity,
)


def _worst(*severities: Severity) -> Severity:
    return max(severities, key=lambda s: SEVERITY_RANK[s])


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that several specialists reached the same conclusion about.

    Keeps the highest-confidence one and records how many **distinct lanes**
    agreed.

    Agreement is signal, not noise: two independent specialists landing on the
    same conclusion is the strongest evidence this pipeline can produce, so it
    has to survive the merge rather than be discarded as a duplicate. That makes
    it a field worth getting exactly right — and an earlier version got it
    wrong twice over. It counted *findings* rather than lanes, so a single lane
    reporting two separate issues on one line produced one finding claiming "2
    lanes agree", and the second issue was lost outright. On a project whose
    named primary failure mode is rubber-stamping an almost-right review, an
    inflated corroboration badge is the worst possible field to inflate.

    So: the key includes `category` (two different conclusions about one line are
    two findings, not one), and `agreement` counts distinct `agent_type` values.
    A lane cannot corroborate itself.

    Severity is taken as the **worst** across the agreeing lanes, not the
    winner's. If security says CRITICAL and quality says MINOR about the same
    thing, the review must surface CRITICAL — deduplication must never be able to
    downgrade a risk, because the whole point of the security lane is that its
    misses are the expensive ones.
    """
    best: dict[tuple[str, int, str], Finding] = {}
    lanes: dict[tuple[str, int, str], set[AgentType]] = {}
    order: list[tuple[str, int, str]] = []

    for f in findings:
        key = f.merge_key()
        current = best.get(key)
        if current is None:
            best[key] = f
            lanes[key] = {f.agent_type}
            order.append(key)
            continue
        lanes[key].add(f.agent_type)
        winner = f if f.confidence > current.confidence else current
        best[key] = winner.model_copy(update={
            "severity": _worst(winner.severity, current.severity, f.severity),
        })

    return [best[k].model_copy(update={"agreement": len(lanes[k])}) for k in order]


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
