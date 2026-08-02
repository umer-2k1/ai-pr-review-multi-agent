"""GitHub read path.

Adapter layer. Along with `llm.py`, the only module allowed to make an outbound
call — INV-2 checks that, and every call here passes an explicit timeout.

**This module deliberately has no write path.** No `create_review`, no
`create_comment`, no `post`. INV-4 greps for exactly those names, and the token
this project uses is granted read scope only, so the human-approval gate is
enforced at the credential layer as well as in code. A bug cannot escalate the
agent's autonomy, because there is nothing to escalate to.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_TIMEOUT_S: float = 30.0
API_ROOT = "https://api.github.com"


class GitHubError(RuntimeError):
    """Raised when the diff cannot be fetched. Never swallowed silently."""


def _request(url: str, token: str | None, accept: str, timeout_s: float) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "prreview/0.1")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        # INV-2: timeout is always passed, never None.
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as exc:
        raise GitHubError(f"GitHub returned {exc.code} for {url}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError(f"could not reach GitHub for {url}: {exc}") from exc


def fetch_pr_diff(
    repo: str,
    pr_number: int,
    token: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    api_root: str = API_ROOT,
) -> str:
    """Fetch a pull request's unified diff.

    Uses the `.diff` media type rather than the JSON `patch` fields: GitHub
    truncates per-file `patch` on large files, and a truncated patch is exactly
    the over-declared-header case the parser had to be hardened against. Asking
    for the diff directly avoids inviting that class of input.
    """
    token = token or os.environ.get("GITHUB_TOKEN")
    url = f"{api_root}/repos/{repo}/pulls/{pr_number}"
    raw = _request(url, token, "application/vnd.github.v3.diff", timeout_s)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitHubError(f"PR diff for {repo}#{pr_number} is not valid UTF-8: {exc}") from exc


def fetch_pr_metadata(
    repo: str,
    pr_number: int,
    token: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    api_root: str = API_ROOT,
) -> dict[str, object]:
    """Title/author/base, for the draft header. Read-only."""
    token = token or os.environ.get("GITHUB_TOKEN")
    url = f"{api_root}/repos/{repo}/pulls/{pr_number}"
    raw = _request(url, token, "application/vnd.github+json", timeout_s)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubError(f"malformed JSON from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GitHubError(f"unexpected payload shape from {url}")
    return {
        "title": payload.get("title"),
        "author": (payload.get("user") or {}).get("login"),
        "base": (payload.get("base") or {}).get("ref"),
        "head": (payload.get("head") or {}).get("ref"),
        "changed_files": payload.get("changed_files"),
    }
