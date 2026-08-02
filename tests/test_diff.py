"""Diff parsing — the grounding truth everything else is checked against."""

from __future__ import annotations

from pathlib import Path

from prreview.diff import parse_diff

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.diff"


def test_parses_both_files() -> None:
    diff = parse_diff(FIXTURE.read_text())
    assert diff.paths == ["app/db.py", "app/client.py"]


def test_hunk_ranges_are_new_side() -> None:
    diff = parse_diff(FIXTURE.read_text())
    db = diff.by_path("app/db.py")
    assert db is not None
    assert db.hunks[0].start == 10
    assert db.hunks[0].end == 23  # 10 + 14 - 1


def test_added_lines_are_captured_with_numbers() -> None:
    diff = parse_diff(FIXTURE.read_text())
    db = diff.by_path("app/db.py")
    assert db is not None
    joined = " ".join(db.added_lines.values())
    assert "SELECT * FROM users" in joined
    assert "API_KEY" in joined


def test_deleted_file_is_skipped() -> None:
    text = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n"
    assert parse_diff(text).files == []


def test_grounding_predicate() -> None:
    diff = parse_diff(FIXTURE.read_text())
    assert diff.is_grounded("app/db.py", 15, 15) is True
    assert diff.is_grounded("app/db.py", 9999, 9999) is False
    assert diff.is_grounded("does/not/exist.py", 1, 1) is False
    assert diff.is_grounded("app/db.py", 20, 10) is False  # inverted range
