"""Command-line entry point.

Entry layer — the only place allowed to touch argv, stdout and the filesystem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prreview.agents.security import SecuritySpecialist
from prreview.contracts import AgentResult, Finding
from prreview.diff import parse_diff
from prreview.llm import AnthropicLLM, LLMClient, OfflineLLM

_AGENTS = {"security": SecuritySpecialist}


def _client(agent: str, offline: bool) -> LLMClient:
    return OfflineLLM(agent_type=agent) if offline else AnthropicLLM()


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


def _cmd_run(args: argparse.Namespace) -> int:
    diff_path = Path(args.diff)
    if not diff_path.is_file():
        print(f"error: no such diff file: {diff_path}", file=sys.stderr)
        return 2

    try:
        diff_text = diff_path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        # Bad input is exit 2, same as every other bad-input branch. Letting this
        # traceback out would exit 1, which is the "a lane failed" code — two very
        # different problems must not share an exit status.
        print(f"error: cannot read {diff_path}: {exc}", file=sys.stderr)
        return 2

    diff = parse_diff(diff_text)
    if not diff.files:
        # Distinguish "not a diff" from "a valid diff with nothing to review".
        # A delete-only PR is legitimate input; calling it malformed is a lie.
        looks_like_a_diff = any(
            line.startswith(("diff --git", "--- ", "+++ ", "@@"))
            for line in diff_text.splitlines()
        )
        if looks_like_a_diff:
            print(f"note: {diff_path} contains no reviewable post-image lines "
                  f"(deletions only, or an unsupported combined/merge diff).", file=sys.stderr)
            return 0
        print(f"error: no files parsed from {diff_path} — is it a unified diff?", file=sys.stderr)
        return 2

    agent_name = args.agent
    specialist = _AGENTS[agent_name](_client(agent_name, args.offline))
    result = specialist.review(diff)

    if args.json:
        print(json.dumps({
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
        }, indent=2))
    else:
        print(_render_text(result))

    # A specialist that failed is a failed run, even though it did not crash.
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr-review", description="Multi-agent PR reviewer.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Review a diff.")
    run.add_argument("--diff", required=True, help="Path to a unified diff file.")
    run.add_argument("--agent", default="security", choices=sorted(_AGENTS), help="Specialist to run.")
    run.add_argument("--offline", action="store_true", help="Use the deterministic offline client (no network, no API key).")
    run.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
