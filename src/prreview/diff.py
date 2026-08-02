"""Unified-diff parsing.

Core layer: imports nothing else from `prreview` (INV-1).

This module exists to answer one question the LLM is not allowed to answer for
itself: *does this line actually exist in this diff?* Findings are checked against
it (INV-3) because a finding citing a nonexistent location destroys trust in every
other finding in the review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# @@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (.+?)[ \t]*$")
# Combined diff (`git show` on a merge): @@@ -1,3 -1,3 +1,4 @@@. Three-way format,
# not what a two-dot PR diff looks like. Detected so it can be skipped loudly
# rather than silently misparsed into a file with zero hunks.
_COMBINED_HUNK_RE = re.compile(r"^@@@+ ")


@dataclass(frozen=True)
class Hunk:
    """A contiguous range of lines in the post-image of a file."""

    start: int
    end: int

    def contains(self, line: int) -> bool:
        return self.start <= line <= self.end


@dataclass
class FileDiff:
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    added_lines: dict[int, str] = field(default_factory=dict)

    def contains_line(self, line: int) -> bool:
        return any(h.contains(line) for h in self.hunks)


@dataclass
class Diff:
    files: list[FileDiff] = field(default_factory=list)

    def by_path(self, path: str) -> FileDiff | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    def is_grounded(self, path: str, line_start: int, line_end: int) -> bool:
        """True only if the whole range lies inside a single hunk of that file.

        This is the INV-3 predicate, and it is deliberately **containment, not
        overlap**. An earlier version asked whether the range touched any hunk
        line, which let a finding claiming lines 1..100000 pass on a diff whose
        only hunk was 10..23 — neither endpoint real, accepted purely because the
        span straddled the hunk. The CLI then printed a location that does not
        exist, which is precisely the failure INV-3 exists to prevent.

        Containment is also O(number of hunks) instead of O(range size). The old
        form enumerated every line, so a model emitting `line_end: 1000000000`
        bought minutes of dead spin per finding — an unbounded wait driven by
        untrusted model output.
        """
        fd = self.by_path(path)
        if fd is None:
            return False
        if line_end < line_start:
            return False
        return any(h.start <= line_start and line_end <= h.end for h in fd.hunks)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


def _clean_path(raw: str) -> str:
    """Normalise a path from a `+++` header.

    git quotes paths containing non-ASCII or special characters and escapes them
    octally: `+++ "b/wei\\303\\237.txt"`. Left as-is, a finding on such a file
    carries a path that matches nothing.
    """
    path = raw.strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        try:
            path = path[1:-1].encode("latin-1").decode("unicode_escape")
            path = path.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            path = raw.strip()[1:-1]
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def parse_diff(text: str) -> Diff:
    """Parse a unified diff into files and post-image hunk ranges.

    Only the new-side (+) numbering is tracked, because findings point at the code
    as it will exist after the merge — that is what a reviewer comments on.
    """
    diff = Diff()
    current: FileDiff | None = None
    new_line = 0

    # Remaining declared body lines for the hunk being consumed. The hunk header
    # states exactly how many old-side and new-side lines follow, so the body's
    # extent is *known*, not guessed.
    #
    # An earlier version inferred the boundary from line prefixes, which is not
    # sound: git renders a deleted line whose content starts with "-- " as
    # "--- <content>", indistinguishable by prefix from a file header. That ended
    # the hunk early, and a following added line beginning "++ " was then read as
    # a new file — inventing a phantom path and silently dropping the real file's
    # line. On a valid patch, in the security lane. Counting is the fix; prefixes
    # cannot distinguish content from structure in this format.
    old_left = 0
    new_left = 0

    # The hunk currently being consumed, as (file, index-into-its-hunks, start).
    # Its `end` is provisional until the body has been read — see _settle().
    pending: tuple[FileDiff, int, int] | None = None
    skipping = False

    def _settle() -> None:
        """Replace a hunk's declared extent with the extent actually consumed.

        The header's counts bound the *body*, but they cannot be trusted to define
        the *grounded range*, because a header can disagree with its body:

        * Over-declared (`@@ -1,200 +1,220 @@` with two body lines) — routine for
          truncated patches, e.g. GitHub's per-file `patch` field on a large file,
          or `git diff | head`. The header claimed 220 lines, so `is_grounded`
          accepted line 219, which the parser never saw. A false accept on INV-3,
          and a direct contradiction of M1's "verified present in the parsed diff".
        * Under-declared (`@@ -1,3 +1,1 @@` with a 4-line body) — the parser reads
          line 2, shows line 2 to the model, then rejects a correct finding on
          line 2 as ungrounded. A false reject.

        On a well-formed diff the consumed extent equals the declared extent, so
        this is a no-op on valid input. It only bites when the header lies.
        """
        nonlocal pending
        if pending is None:
            return
        fd, idx, start = pending
        seen_end = new_line - 1
        if seen_end >= start:
            fd.hunks[idx] = Hunk(start=start, end=seen_end)
        elif 0 <= idx < len(fd.hunks):
            # Body contributed no new-side lines at all (pure deletion): the hunk
            # grounds nothing, so remove it rather than leave a phantom range.
            fd.hunks.pop(idx)
        pending = None

    # `str.splitlines()` is wrong here: it also breaks on \x0b \x0c \x1c \x1d \x1e
    # \x85    , which are *content* in a diff, not line terminators. A
    # form-feed page break (a documented convention in GNU C, Emacs Lisp and
    # PEP 8) or a   in a JS string would split one added line into two —
    # truncating the recorded content, shifting every later line number, and
    # corrupting the body counters. Same failure class as BD-1, different door,
    # and worse: the shifted line still lands inside the declared hunk, so
    # is_grounded() returns True and INV-3 reports a false green.
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()

    for raw in lines:
        in_hunk = old_left > 0 or new_left > 0

        if not in_hunk:
            if pending is not None:
                _settle()

            if raw.startswith("diff --git") or raw.startswith("--- "):
                skipping = False

            if skipping:
                continue

            m_file = _NEW_FILE_RE.match(raw)
            if raw.startswith("+++ ") and m_file:
                path = _clean_path(m_file.group(1))
                if path == "/dev/null":  # deleted file — nothing to review
                    current = None
                    continue
                current = FileDiff(path=path)
                diff.files.append(current)
                continue

            if _COMBINED_HUNK_RE.match(raw):
                # Combined/merge diff (`git show --cc` on a merge). There is no
                # single post-image to cite, so refuse the file outright rather
                # than misparse it. `skipping` suppresses the body too: a combined
                # body line beginning "+ " renders as "+++ …" and would otherwise
                # invent a phantom path.
                if current is not None and current in diff.files:
                    diff.files.remove(current)
                current = None
                skipping = True
                continue

            if raw.startswith("@@"):
                m_hunk = _HUNK_RE.match(raw)
                if m_hunk and current is not None:
                    old_count = int(m_hunk.group(2)) if m_hunk.group(2) is not None else 1
                    start = int(m_hunk.group(3))
                    count = int(m_hunk.group(4)) if m_hunk.group(4) is not None else 1
                    new_line = start
                    old_left, new_left = old_count, count
                    if count > 0:
                        current.hunks.append(Hunk(start=start, end=start + count - 1))
                        pending = (current, len(current.hunks) - 1, start)
                continue
            continue

        # ── inside a hunk body: every line here is content, never structure ──
        if current is None:  # pragma: no cover - defensive
            continue

        if raw.startswith("+"):
            current.added_lines[new_line] = raw[1:]
            new_line += 1
            new_left -= 1
        elif raw.startswith("-"):
            old_left -= 1  # old side only; does not advance new-side numbering
        elif raw.startswith("\\"):
            pass  # "\ No newline at end of file" — a note, not a line
        else:
            # An unmodified context line advances the new-side counter. `git diff`
            # writes a blank context line as a single space, but editors, mail
            # clients and copy-paste routinely strip trailing whitespace, leaving
            # "". Treating that as "not a line" silently shifts every subsequent
            # line number by one — findings then cite locations that are off by
            # the number of blank context lines above them.
            new_line += 1
            old_left -= 1
            new_left -= 1

    _settle()  # a diff truncated mid-hunk still gets an honest range
    return diff


# Characters Python treats as line boundaries but a diff treats as content.
# Anything here must be escaped before it reaches a prompt — see _escape_content.
_LINE_SEPARATORS = "\x0b\x0c\x1c\x1d\x1e\x85  "
_ESCAPE_TABLE = {ord(ch): f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}"
                 for ch in _LINE_SEPARATORS}


def _escape_content(content: str) -> str:
    """Render content so one source line is always one physical prompt line.

    The prompt's whole contract is `<line_no>: <content>`, which only holds if
    content cannot itself contain a line break. A form feed or U+2028 inside an
    added line splits the entry in two: the tail becomes an orphan line that any
    consumer re-reading the prompt will mis-attribute to whatever number the tail
    happens to start with, and the head loses the rest of its content.

    That produced both halves of a real failure — a CRITICAL finding pinned to an
    unmodified context line, and a credential on a split line silently missed.
    Escaping here fixes the live model path too, which was receiving the same
    mangled prompt.
    """
    return content.translate(_ESCAPE_TABLE)


def render_for_prompt(diff: Diff, max_chars: int = 12000) -> str:
    """Flatten a parsed diff into the text a specialist actually sees.

    Line numbers are prefixed so the model has something concrete to cite. It is
    still checked afterwards — prompting is not enforcement.
    """
    out: list[str] = []
    for f in diff.files:
        out.append(f"--- FILE: {_escape_content(f.path)}")
        for line_no in sorted(f.added_lines):
            out.append(f"{line_no}: {_escape_content(f.added_lines[line_no])}")
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… [truncated]"
    return text
