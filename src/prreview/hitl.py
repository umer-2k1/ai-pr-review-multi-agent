"""The human-in-the-loop gate.

Domain layer. Imports `contracts` only.

The autonomy level chosen at G0 is the study's level 2, "human reviews output":
the agent drafts, a human approves, and only then does anything reach the PR.
This module decides which of two things the human is being asked to do — glance
at a draft, or read it properly — and it is deliberately conservative about the
difference.

Nothing here posts anywhere. INV-4 forbids a write path existing at all, and the
GitHub token this project uses has no write scope, so the gate is enforced at the
credential layer as well as in code.
"""

from __future__ import annotations

from prreview.contracts import (
    AgentResult,
    Finding,
    HitlVerdict,
    Severity,
)

# Below this, a draft is not trustworthy enough to hand over with a glance.
CONFIDENCE_THRESHOLD: float = 0.70

# Each failed lane costs this much of the roll-up. A lane that did not finish is
# missing evidence, not evidence of absence.
FAILED_LANE_PENALTY: float = 0.25

# What an empty-but-complete review is worth. Not 1.0: "we looked and found
# nothing" is a real result, but it is not a certainty, and a system that reports
# perfect confidence in silence teaches its reader to stop checking.
EMPTY_REVIEW_CONFIDENCE: float = 0.80


def compute_confidence(findings: list[Finding], failed_lanes: int) -> float:
    """Roll individual confidences into one number for the gate.

    Deliberately simple and explainable — a reviewer must be able to work out why
    the gate held. The mean is used rather than the max because a single
    high-confidence finding should not make a review of six shaky ones look
    trustworthy.
    """
    base = (
        sum(f.confidence for f in findings) / len(findings)
        if findings
        else EMPTY_REVIEW_CONFIDENCE
    )
    if failed_lanes:
        base *= max(0.0, 1.0 - FAILED_LANE_PENALTY * failed_lanes)
    return round(min(max(base, 0.0), 1.0), 3)


def decide(
    findings: list[Finding],
    failed_lanes: int,
    lanes_nothing_usable: int = 0,
    confidence: float | None = None,
) -> tuple[HitlVerdict, str]:
    """Return the gate verdict and the one-line reason it fired.

    The reason is part of the contract, not decoration: a gate that holds without
    saying why trains people to click through it.

    HOLD wins over DRAFT_READY in every ambiguous case. The asymmetry is
    deliberate — a needless HOLD costs a human two minutes, while a wrongly
    released draft costs the credibility of every review after it.
    """
    score = compute_confidence(findings, failed_lanes) if confidence is None else confidence

    # A CRITICAL finding always goes to a human, at any confidence. This is not a
    # threshold decision: the consequence of being wrong about a missed SQL
    # injection is categorically different from being wrong about a style nit,
    # and the study's own HITL spectrum picks the level from consequence first.
    if any(f.severity is Severity.CRITICAL for f in findings):
        return HitlVerdict.HOLD, "a CRITICAL finding is present"

    if failed_lanes:
        return HitlVerdict.HOLD, (
            f"{failed_lanes} specialist lane(s) did not finish — the review is incomplete"
        )

    if lanes_nothing_usable:
        return HitlVerdict.HOLD, (
            f"{lanes_nothing_usable} lane(s) produced findings but kept none — a malfunction"
        )

    if score < CONFIDENCE_THRESHOLD:
        return HitlVerdict.HOLD, (
            f"overall confidence {score:.2f} is below the {CONFIDENCE_THRESHOLD:.2f} threshold"
        )

    return HitlVerdict.DRAFT_READY, f"confidence {score:.2f}, no CRITICAL findings, all lanes reported"


def render_draft(review_id: str, findings: list[Finding], verdict: HitlVerdict, reason: str) -> str:
    """The markdown a human reads before deciding to post it.

    Produced for every verdict, including HOLD — the point of the gate is that a
    person reads the draft, so withholding the draft would defeat it.
    """
    lines = [
        f"## Automated review `{review_id}`",
        "",
        f"**Status:** {verdict.value} — {reason}",
        "",
        "> Drafted by an automated reviewer. A human must approve before this is posted.",
        "",
    ]
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)

    lines.append(f"**{len(findings)} finding(s):**")
    lines.append("")
    for f in findings:
        agree = f" _({f.agreement} lanes agree)_" if f.agreement > 1 else ""
        lines.append(
            f"### {f.severity.value.upper()} — `{f.file_path}:{f.line_start}` "
            f"({f.category}){agree}"
        )
        lines.append("")
        lines.append(f.summary)
        lines.append("")
        lines.append(f"- **Why:** {f.rationale}")
        if f.suggestion:
            lines.append(f"- **Suggested fix:** {f.suggestion}")
        lines.append(f"- **Confidence:** {f.confidence:.2f} (agent: {f.agent_type.value})")
        lines.append("")
    return "\n".join(lines)


def count_lanes_nothing_usable(results: list[AgentResult]) -> int:
    return sum(1 for r in results if r.produced_nothing_usable)
