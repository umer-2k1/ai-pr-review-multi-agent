"""End-to-end CLI tests that go through the real file-read path.

Every other test file calls `parse_diff` on a Python string literal. That left
the read itself untested — and `read_text()`'s universal-newline translation was
silently rewriting diffs before the parser ever saw them, mis-locating findings
on input the parser handled correctly. Tests that start from a string can never
catch that class of bug; these start from bytes on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prreview.cli import main

SEPARATORS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


@pytest.mark.parametrize("sep", SEPARATORS, ids=lambda s: f"U+{ord(s):04X}")
def test_separator_in_content_does_not_mislocate_via_the_file_path(
    sep: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R5-1 regression, exercised the way a user actually runs the tool.

    The SQL injection is on line 5. A separator inside line 2 must not shift it.
    """
    diff = (
        "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,6 @@\n ctx\n"
        f'+BANNER = "loading 10%{sep}done"\n'
        "+def find(conn, name):\n"
        "+    cur = conn.cursor()\n"
        "+    cur.execute(f\"SELECT * FROM u WHERE n = '{name}'\")\n"
        "+    return cur.fetchone()\n"
    )
    path = tmp_path / "mis.diff"
    path.write_bytes(diff.encode())

    code, out = _run(["run", "--diff", str(path), "--offline", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    sqli = [f for f in payload["findings"] if f["category"] == "sql-injection"]
    assert sqli, "the injection was lost entirely"
    assert sqli[0]["line_start"] == 5, (
        f"injection mis-located to line {sqli[0]['line_start']}; it is on line 5"
    )


@pytest.mark.parametrize("sep", SEPARATORS, ids=lambda s: f"U+{ord(s):04X}")
def test_separator_does_not_hide_a_credential_via_the_file_path(
    sep: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = (
        "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,3 @@\n ctx\n"
        f'+BANNER = "loading 10%{sep}loading 100%"\n'
        '+API_KEY = "sk-live-SHIFTED"\n'
    )
    path = tmp_path / "shift.diff"
    path.write_bytes(diff.encode())

    code, out = _run(["run", "--diff", str(path), "--offline", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert any(f["category"] == "hardcoded-secret" for f in payload["findings"]), (
        "the credential was silently lost"
    )


def test_crlf_diff_still_parses_correctly_from_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The read fix must not break the ordinary Windows case."""
    diff = (
        "--- a/app.py\r\n+++ b/app.py\r\n@@ -1,1 +1,2 @@\r\n ctx\r\n"
        '+API_KEY = "sk-live-crlf"\r\n'
    )
    path = tmp_path / "crlf.diff"
    path.write_bytes(diff.encode())

    code, out = _run(["run", "--diff", str(path), "--offline", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    secrets = [f for f in payload["findings"] if f["category"] == "hardcoded-secret"]
    assert secrets and secrets[0]["line_start"] == 2


def test_demo_command_from_plan_md(capsys: pytest.CaptureFixture[str]) -> None:
    """M1's contractual demo command, asserted rather than eyeballed."""
    code, out = _run(
        ["run", "--diff", "fixtures/sample.diff", "--agent", "security", "--offline", "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    located = {(f["file_path"], f["line_start"], f["category"]) for f in payload["findings"]}
    assert ("app/db.py", 10, "sql-injection") in located
    assert ("app/db.py", 14, "hardcoded-secret") in located


def test_incomplete_review_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partially-failed fan-out must not read as a pass in CI.

    Previously untested: replacing `return 1 if review.incomplete else 0` with
    `return 0` left the whole suite green.
    """
    from prreview.contracts import AgentType
    from prreview.llm import FailingLLM, OfflineLLM

    def factory(offline: bool):  # type: ignore[no-untyped-def]
        return lambda at: FailingLLM() if at is AgentType.QUALITY else OfflineLLM(at.value)

    monkeypatch.setattr("prreview.cli._client_factory", factory)
    assert main(["run", "--diff", "fixtures/sample.diff", "--offline"]) == 1


def test_complete_review_exits_0() -> None:
    assert main(["run", "--diff", "fixtures/sample.diff", "--offline"]) == 0


def test_m3_demo_command(capsys: pytest.CaptureFixture[str]) -> None:
    """M3's contractual demo command, asserted rather than eyeballed.

    Previously unpinned: no CLI-level test asserted the gate fields at all.
    """
    code, out = _run(["run", "--diff", "fixtures/critical.diff", "--offline", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["hitl_verdict"] == "HOLD"
    assert payload["max_severity"] == "critical"
    assert "CRITICAL" in payload["hitl_reason"]
    # The point of the criterion: it holds DESPITE high confidence.
    assert payload["overall_confidence"] > 0.7


def test_draft_output_is_produced_and_says_a_human_must_approve(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = _run(["run", "--diff", "fixtures/critical.diff", "--offline", "--draft"], capsys)
    assert code == 0
    assert "HOLD" in out
    assert "human must approve" in out.lower()
    assert "app/auth.py" in out


def test_draft_is_emitted_even_when_the_gate_holds(capsys: pytest.CaptureFixture[str]) -> None:
    """Withholding the draft on HOLD would defeat the gate: the whole point is
    that a person reads it."""
    _, out = _run(["run", "--diff", "fixtures/critical.diff", "--offline", "--draft"], capsys)
    assert "finding(s)" in out, "HOLD suppressed the findings a human needs to read"


class TestEventsSpineCLI:
    """M4's CLI wiring. Previously untested end to end — deleting the
    `gate_decided` emit, or the total-cost line, left the whole suite green."""

    def test_m4_demo_command(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        events = tmp_path / "events.jsonl"
        assert main(["run", "--diff", "fixtures/sample.diff", "--offline",
                     "--events", str(events)]) == 0
        capsys.readouterr()

        code, out = _run(["trace", "--last", "--events", str(events)], capsys)
        assert code == 0
        assert "total tokens:" in out, "M4 requires the total cost to be printed"

    def test_a_full_run_emits_at_least_seven_rows(self, tmp_path: Path) -> None:
        """The stated criterion. The orchestrator alone emits 6; the 7th is the
        gate decision, which only the CLI knows about."""
        from prreview.events import read_log

        events = tmp_path / "events.jsonl"
        main(["run", "--diff", "fixtures/sample.diff", "--offline", "--events", str(events)])

        rows = read_log(events)
        assert len(rows) >= 7, f"only {len(rows)} rows; the gate decision is missing"
        assert any(e.event_type == "gate_decided" for e in rows)
        assert [e.seq for e in rows] == sorted(e.seq for e in rows)

    def test_every_row_carries_the_required_fields(self, tmp_path: Path) -> None:
        from prreview.events import read_log

        events = tmp_path / "events.jsonl"
        main(["run", "--diff", "fixtures/sample.diff", "--offline", "--events", str(events)])
        for e in read_log(events):
            assert e.review_id and e.event_type
            assert e.tokens >= 0 and e.duration_ms >= 0

    def test_all_rows_share_one_review_id(self, tmp_path: Path) -> None:
        from prreview.events import read_log

        events = tmp_path / "events.jsonl"
        main(["run", "--diff", "fixtures/sample.diff", "--offline", "--events", str(events)])
        assert len({e.review_id for e in read_log(events)}) == 1

    def test_no_events_flag_writes_nothing(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        assert main(["run", "--diff", "fixtures/sample.diff", "--offline",
                     "--no-events", "--events", str(events)]) == 0
        assert not events.exists()

    def test_trace_on_an_empty_log_exits_2(self, tmp_path: Path) -> None:
        assert main(["trace", "--last", "--events", str(tmp_path / "nope.jsonl")]) == 2

    def test_trace_selects_by_review_id(self, tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
        from prreview.events import read_log

        events = tmp_path / "events.jsonl"
        main(["run", "--diff", "fixtures/sample.diff", "--offline", "--events", str(events)])
        main(["run", "--diff", "fixtures/critical.diff", "--offline", "--events", str(events)])
        capsys.readouterr()

        first_id = read_log(events)[0].review_id
        code, out = _run(["trace", "--review-id", first_id, "--events", str(events)], capsys)
        assert code == 0
        assert first_id in out

    def test_an_unwritable_events_path_does_not_destroy_the_review(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """BD-1 regression.

        Observability must not be able to destroy the thing it observes. A
        read-only working directory used to kill a fully-computed review with an
        uncaught PermissionError — on the FIRST event, before any lane ran.
        """
        blocked = tmp_path / "a-file-not-a-dir"
        blocked.write_text("i am a file")
        target = blocked / "events.jsonl"  # parent is a file: mkdir must fail

        code, out = _run(["run", "--diff", "fixtures/sample.diff", "--offline",
                          "--events", str(target)], capsys)

        assert code == 0, "a logging failure killed the run"
        assert "app/db.py" in out, "the review was discarded because a log line failed"

    def test_the_operator_is_told_the_spine_is_broken(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        blocked = tmp_path / "blocker"
        blocked.write_text("x")
        main(["run", "--diff", "fixtures/sample.diff", "--offline",
              "--events", str(blocked / "e.jsonl")])
        assert "events spine disabled" in capsys.readouterr().err


def test_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--diff", str(tmp_path / "nope.diff"), "--offline"]) == 2


def test_undecodable_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bin.diff"
    path.write_bytes(b"\xff\xfe\x00\x01binary")
    assert main(["run", "--diff", str(path), "--offline"]) == 2


def test_junk_that_is_not_a_diff_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "junk.diff"
    path.write_text("this is just prose, not a diff at all\n")
    assert main(["run", "--diff", str(path), "--offline"]) == 2


def test_delete_only_diff_exits_0_with_a_note(tmp_path: Path) -> None:
    """A valid diff with nothing reviewable is not a usage error."""
    path = tmp_path / "del.diff"
    path.write_text("--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n-y = 2\n")
    assert main(["run", "--diff", str(path), "--offline"]) == 0
