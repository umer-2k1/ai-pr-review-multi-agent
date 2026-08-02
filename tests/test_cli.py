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
