"""The GitHub read path.

The most important tests here are the ones asserting what does NOT exist: this
module is where a write path would live, and INV-4 forbids one. A test that
merely checks the current code has no write call is weak; these check the
structural properties that keep it that way.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from prreview import github
from prreview.github import GitHubError, fetch_pr_diff, fetch_pr_metadata

SRC = Path(__file__).parent.parent / "src" / "prreview"


class TestNoWritePath:
    """INV-4, checked structurally rather than by grepping for today's names."""

    def test_the_module_exposes_no_write_function(self) -> None:
        names = [n for n, _ in inspect.getmembers(github, inspect.isfunction)]
        forbidden = re.compile(r"(post|create|update|delete|comment|approve|merge|submit)", re.I)
        offenders = [n for n in names if forbidden.search(n)]
        assert offenders == [], f"a write-shaped function appeared: {offenders}"

    def test_no_module_issues_a_non_get_request(self) -> None:
        """A POST/PATCH/PUT/DELETE anywhere in the tree would be a write path."""
        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            text = path.read_text()
            for verb in ("POST", "PATCH", "PUT", "DELETE"):
                if f'"{verb}"' in text or f"'{verb}'" in text or f"method={verb}" in text:
                    offenders.append(f"{path.name}: {verb}")
        assert offenders == [], f"a non-GET HTTP method appeared: {offenders}"

    def test_requests_carry_no_body(self) -> None:
        source = inspect.getsource(github._request)
        assert "data=" not in source and "json=" not in source, (
            "a request body means something is being written"
        )


class TestTimeouts:
    """INV-2: every outbound call has an explicit timeout."""

    def test_request_passes_a_timeout(self) -> None:
        assert "timeout=" in inspect.getsource(github._request)

    def test_public_functions_expose_a_timeout(self) -> None:
        for fn in (fetch_pr_diff, fetch_pr_metadata):
            assert "timeout_s" in inspect.signature(fn).parameters

    def test_default_timeout_is_finite(self) -> None:
        assert 0 < github.DEFAULT_TIMEOUT_S < 300


class TestFetch:
    def test_diff_is_requested_as_a_diff_not_as_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GitHub truncates the per-file `patch` field on large files, and a
        truncated patch is exactly the over-declared-header case the parser had
        to be hardened against. Asking for `.diff` avoids inviting it."""
        seen: dict[str, object] = {}

        def fake(url: str, token: str | None, accept: str, timeout_s: float) -> bytes:
            seen["accept"] = accept
            seen["url"] = url
            return b"--- a/x\n+++ b/x\n@@ -1,1 +1,2 @@\n c\n+n\n"

        monkeypatch.setattr(github, "_request", fake)
        fetch_pr_diff("o/r", 7, token="t")
        assert seen["accept"] == "application/vnd.github.v3.diff"
        assert seen["url"] == "https://api.github.com/repos/o/r/pulls/7"

    def test_a_failed_fetch_raises_githuberror_not_a_raw_urllib_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: object, **k: object) -> bytes:
            raise OSError("network is down")

        monkeypatch.setattr(github.urllib.request, "urlopen", boom)
        with pytest.raises(GitHubError) as excinfo:
            fetch_pr_diff("o/r", 1, token="t")
        assert "could not reach GitHub" in str(excinfo.value)

    def test_non_utf8_diff_is_a_clear_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "_request", lambda *a, **k: b"\xff\xfe not utf8")
        with pytest.raises(GitHubError) as excinfo:
            fetch_pr_diff("o/r", 1, token="t")
        assert "not valid UTF-8" in str(excinfo.value)

    def test_metadata_extracts_only_what_the_draft_header_needs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "title": "Add thing", "user": {"login": "alice"},
            "base": {"ref": "main"}, "head": {"ref": "feature"},
            "changed_files": 3, "body": "should not be extracted",
        }
        monkeypatch.setattr(github, "_request", lambda *a, **k: json.dumps(payload).encode())
        meta = fetch_pr_metadata("o/r", 1, token="t")
        assert meta == {
            "title": "Add thing", "author": "alice", "base": "main",
            "head": "feature", "changed_files": 3,
        }

    def test_malformed_metadata_json_is_a_clear_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "_request", lambda *a, **k: b"{not json")
        with pytest.raises(GitHubError):
            fetch_pr_metadata("o/r", 1, token="t")

    def test_missing_nested_keys_do_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "_request", lambda *a, **k: b'{"title": "t"}')
        meta = fetch_pr_metadata("o/r", 1, token="t")
        assert meta["author"] is None and meta["base"] is None
