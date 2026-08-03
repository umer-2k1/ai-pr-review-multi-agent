"""Command-line entry point.

Entry layer — the only place allowed to touch argv, stdout and the filesystem.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from prreview.agents.base import Specialist
from prreview.agents.docs import DocsSpecialist
from prreview.agents.quality import QualitySpecialist
from prreview.agents.security import SecuritySpecialist
from prreview.agents.tests import TestsSpecialist
from prreview.contracts import AgentResult, AgentType, Finding, HitlVerdict, Review
from prreview.diff import parse_diff
from prreview.events import (
    DEFAULT_EVENTS_DIR,
    EventLog,
    format_trace,
    latest_review_id,
    read_log,
)
from prreview.github import GitHubError, fetch_pr_diff
from prreview.hitl import render_draft
from prreview.llm import AnthropicLLM, LLMClient, OfflineLLM
from prreview.orchestrator import run_review

DEFAULT_EVENTS_PATH = DEFAULT_EVENTS_DIR / "events.jsonl"

# Typed as factories rather than `type[Specialist]`: the base is abstract, so a
# mapping of the class objects reads to mypy as "may instantiate the ABC".
_AGENTS: dict[str, Callable[[LLMClient], Specialist]] = {
    "security": SecuritySpecialist,
    "quality": QualitySpecialist,
    "tests": TestsSpecialist,
    "docs": DocsSpecialist,
}


def _client(agent: str, offline: bool) -> LLMClient:
    return OfflineLLM(agent_type=agent) if offline else AnthropicLLM()


def _client_factory(offline: bool) -> Callable[[AgentType], LLMClient]:
    return lambda at: _client(at.value, offline)


def _finding_dict(f: Finding) -> dict[str, object]:
    return {
        "agent_type": f.agent_type.value,
        "severity": f.severity.value,
        "category": f.category,
        "summary": f.summary,
        "file_path": f.file_path,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "suggestion": f.suggestion,
        "confidence": round(f.confidence, 3),
        "rationale": f.rationale,
        "agreement": f.agreement,
    }


def _render_text(result: AgentResult) -> str:
    lines: list[str] = []
    status = "ok" if result.ok else f"FAILED ({result.error})"
    lines.append(f"agent: {result.agent_type.value}  [{status}]")
    lines.append(f"findings: {len(result.findings)}   tokens: {result.tokens}   {result.duration_ms}ms")
    if result.dropped_ungrounded:
        lines.append(
            f"dropped: {result.dropped_ungrounded} ungrounded "
            f"(cited a file/line absent from the diff)"
        )
    if result.dropped_malformed:
        lines.append(
            f"dropped: {result.dropped_malformed} malformed "
            f"(did not match the Finding schema)"
        )
    if result.produced_nothing_usable:
        lines.append(
            "WARNING: this lane produced findings and kept NONE of them — "
            "a malfunction, not a clean review."
        )
    lines.append("")
    for f in result.findings:
        lines.append(f"  [{f.severity.value.upper():8}] {f.file_path}:{f.line_start}  ({f.category})")
        lines.append(f"             {f.summary}")
        lines.append(f"             why: {f.rationale}")
        if f.suggestion:
            lines.append(f"             fix: {f.suggestion}")
        lines.append(f"             confidence: {f.confidence:.2f}")
        lines.append("")
    if not result.findings and result.ok:
        lines.append("  no findings in this concern.")
    return "\n".join(lines)


def _result_dict(result: AgentResult) -> dict[str, object]:
    return {
        "agent": result.agent_type.value,
        "ok": result.ok,
        "error": result.error,
        "tokens": result.tokens,
        "duration_ms": result.duration_ms,
        "dropped_ungrounded": result.dropped_ungrounded,
        "dropped_malformed": result.dropped_malformed,
        "all_findings_were_hallucinated": result.all_findings_were_hallucinated,
        "produced_nothing_usable": result.produced_nothing_usable,
        "findings": [_finding_dict(f) for f in result.findings],
    }


def _review_dict(review: Review) -> dict[str, object]:
    return {
        "review_id": review.review_id,
        "agents_run": [a.value for a in review.agents_run],
        "agents_failed": [a.value for a in review.agents_failed],
        "incomplete": review.incomplete,
        "overall_confidence": review.overall_confidence,
        "hitl_verdict": review.hitl_verdict.value,
        "hitl_reason": review.hitl_reason,
        "dropped_ungrounded": review.dropped_ungrounded,
        "max_severity": review.max_severity.value if review.max_severity else None,
        "findings": [_finding_dict(f) for f in review.findings],
    }


def _render_review(review: Review) -> str:
    lines: list[str] = []
    lines.append(f"review {review.review_id}")
    lines.append(
        f"agents run: {len(review.agents_run)}   findings: {len(review.findings)}"
        + (f"   FAILED LANES: {', '.join(a.value for a in review.agents_failed)}"
           if review.agents_failed else "")
    )
    lines.append(
        f"confidence: {review.overall_confidence:.2f}   "
        f"gate: {review.hitl_verdict.value} — {review.hitl_reason}"
    )
    if review.hitl_verdict is HitlVerdict.DRAFT_READY:
        lines.append("A human must still approve this draft before it is posted.")
    if review.incomplete:
        lines.append(
            "WARNING: this review is INCOMPLETE — one or more specialists did not "
            "finish. Absence of findings in those concerns is not evidence of absence."
        )
    if review.dropped_ungrounded:
        lines.append(f"dropped: {review.dropped_ungrounded} ungrounded finding(s)")
    lines.append("")
    for f in review.findings:
        agree = f"  [{f.agreement} lanes agree]" if f.agreement > 1 else ""
        lines.append(
            f"  [{f.severity.value.upper():8}] {f.file_path}:{f.line_start}  "
            f"({f.category}, {f.agent_type.value}){agree}"
        )
        lines.append(f"             {f.summary}")
        lines.append(f"             why: {f.rationale}")
        if f.suggestion:
            lines.append(f"             fix: {f.suggestion}")
        lines.append(f"             confidence: {f.confidence:.2f}")
        lines.append("")
    if not review.findings:
        lines.append("  no findings." if not review.incomplete
                     else "  no findings from the lanes that completed.")
    return "\n".join(lines)


def _load_diff_text(args: argparse.Namespace) -> tuple[str | None, int]:
    """Return (diff text, exit code). Exactly one source: --diff or --pr."""
    if args.pr:
        if not args.repo:
            print("error: --pr requires --repo OWNER/NAME", file=sys.stderr)
            return None, 2
        try:
            return fetch_pr_diff(args.repo, args.pr, timeout_s=args.timeout), 0
        except GitHubError as exc:
            # Cannot fetch is bad input, not a failed lane — exit 2, so CI can
            # tell "we could not look" from "we looked and a specialist died".
            print(f"error: {exc}", file=sys.stderr)
            return None, 2

    diff_path = Path(args.diff)
    if not diff_path.is_file():
        print(f"error: no such diff file: {diff_path}", file=sys.stderr)
        return None, 2
    try:
        # newline="" disables universal-newline translation. Without it Python
        # rewrites a lone \r anywhere in the file to \n *before* the parser runs,
        # inserting a phantom line break mid-content: every later line number in
        # that hunk shifts, and the shifted line still sits inside the hunk, so
        # is_grounded() accepts a finding pointed at the wrong line. A lone \r
        # reaches a diff via committed terminal/CI transcripts with progress
        # redraws, classic-Mac line endings, and CRs inside string literals.
        # CRLF is unaffected: the trailing \r is stripped per-line in parse_diff.
        # Path.read_text() only accepts `newline` from 3.13; this package targets
        # >=3.11, so go through open() to stay portable.
        with diff_path.open("r", newline="") as handle:
            diff_text = handle.read()
        return diff_text, 0
    except (OSError, UnicodeDecodeError) as exc:
        # Bad input is exit 2, same as every other bad-input branch. Letting this
        # traceback out would exit 1, which is the "a lane failed" code — two very
        # different problems must not share an exit status.
        print(f"error: cannot read {diff_path}: {exc}", file=sys.stderr)
        return None, 2


def _cmd_run(args: argparse.Namespace) -> int:
    if args.json_out and args.agent:
        # --json-out writes the merged Review; a single lane never produces one.
        # Silently writing an AgentResult under the same flag would hand CI a
        # document with no hitl_verdict, which is the field the gate reads.
        print("error: --json-out applies to the full fan-out, not --agent", file=sys.stderr)
        return 2

    diff_text, code = _load_diff_text(args)
    if diff_text is None:
        return code
    source = f"{args.repo}#{args.pr}" if args.pr else args.diff

    diff = parse_diff(diff_text)
    if not diff.files:
        # Distinguish "not a diff" from "a valid diff with nothing to review".
        # A delete-only PR is legitimate input; calling it malformed is a lie.
        looks_like_a_diff = any(
            line.startswith(("diff --git", "--- ", "+++ ", "@@"))
            for line in diff_text.split("\n")
        )
        if looks_like_a_diff:
            print(f"note: {source} contains no reviewable post-image lines "
                  f"(deletions only, or an unsupported combined/merge diff).", file=sys.stderr)
            return 0
        print(f"error: no files parsed from {source} — is it a unified diff?", file=sys.stderr)
        return 2

    # --agent runs one lane (M1 behaviour, kept for debugging a single concern).
    # Without it, the full four-lane fan-out runs — that is the actual product.
    if args.agent:
        agent_name: str = args.agent
        result = _AGENTS[agent_name](_client(agent_name, args.offline)).review(diff)
        if args.json:
            print(json.dumps(_result_dict(result), indent=2))
        else:
            print(_render_text(result))
        # A specialist that failed is a failed run, even though it did not crash.
        return 0 if result.ok else 1

    log = EventLog(path=None if args.no_events else Path(args.events))

    def sink(event_type: str, agent_type: AgentType | None, result: AgentResult | None) -> None:
        log.emit(review_id_holder[0], event_type, agent_type, result)

    # The id must be known before the first event, so the whole trace shares one.
    review_id_holder = [uuid.uuid4().hex[:12]]
    review = run_review(diff, _client_factory(args.offline),
                        review_id=review_id_holder[0], on_event=sink)
    log.emit(review.review_id, "gate_decided", None, None,
             verdict=review.hitl_verdict.value,
             confidence=review.overall_confidence,
             findings=len(review.findings))

    if args.json_out:
        # Written from the SAME review object that stdout renders, so the draft a
        # human approves, the JSON a CI job thresholds on, and the events trace
        # all describe one review. Producing them from two invocations would let
        # a human approve a draft that is not the artifact that was gated.
        try:
            Path(args.json_out).write_text(
                json.dumps(_review_dict(review), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            print(f"error: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2

    if args.draft:
        # The markdown a human reads before deciding to post it. Produced for
        # HOLD as well as DRAFT_READY — the point of the gate is that a person
        # reads the draft, so withholding it on HOLD would defeat the gate.
        print(render_draft(
            review.review_id, list(review.findings), review.hitl_verdict, review.hitl_reason
        ))
    elif args.json:
        print(json.dumps(_review_dict(review), indent=2))
    else:
        print(_render_review(review))

    # An incomplete review is not a clean review: exit 1 so CI cannot read a
    # partially-failed fan-out as a pass.
    return 1 if review.incomplete else 0


def _cmd_trace(args: argparse.Namespace) -> int:
    path = Path(args.events)
    events = read_log(path)
    if not events:
        print(f"no events recorded in {path}", file=sys.stderr)
        return 2

    review_id = args.review_id
    if args.last or review_id is None:
        # Derived from the rows already read, not a second read of the file.
        review_id = events[-1].review_id

    # Sort by seq, not file order. `seq` is the authoritative ordering — it is
    # assigned under a lock precisely because wall-clock and arrival order are
    # not trustworthy — so the trace must not inherit whatever order the lines
    # happen to sit in.
    selected = sorted((e for e in events if e.review_id == review_id), key=lambda e: e.seq)
    if not selected:
        print(f"no events for review {review_id} in {path}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(e) for e in selected], indent=2))
    else:
        print(format_trace(selected))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr-review", description="Multi-agent PR reviewer.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Review a diff.")
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff", help="Path to a unified diff file.")
    src.add_argument("--pr", type=int, help="Pull request number to fetch (read-only).")
    run.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                     help="OWNER/NAME. Defaults to $GITHUB_REPOSITORY.")
    run.add_argument("--timeout", type=float, default=30.0,
                     help="Timeout in seconds for the GitHub fetch.")
    run.add_argument("--agent", default=None, choices=sorted(_AGENTS),
                     help="Run ONE specialist instead of the full four-lane fan-out.")
    run.add_argument("--offline", action="store_true", help="Use the deterministic offline client (no network, no API key).")
    run.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    run.add_argument("--draft", action="store_true",
                     help="Emit the markdown draft review for a human to approve.")
    run.add_argument("--json-out", default=None, metavar="PATH",
                     help="Also write the review as JSON to PATH. Combine with --draft to "
                          "get both artifacts from ONE fan-out.")
    run.add_argument("--events", default=str(DEFAULT_EVENTS_PATH),
                     help=f"Events spine file (default: {DEFAULT_EVENTS_PATH}).")
    run.add_argument("--no-events", action="store_true",
                     help="Do not write the events spine.")
    run.set_defaults(func=_cmd_run)

    trace = sub.add_parser("trace", help="Reconstruct a review from the events spine.")
    trace.add_argument("--events", default=str(DEFAULT_EVENTS_PATH),
                       help=f"Events spine file (default: {DEFAULT_EVENTS_PATH}).")
    trace.add_argument("--last", action="store_true", help="Trace the most recent review.")
    trace.add_argument("--review-id", default=None, help="Trace a specific review id.")
    trace.add_argument("--json", action="store_true", help="Emit the raw rows as JSON.")
    trace.set_defaults(func=_cmd_trace)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
