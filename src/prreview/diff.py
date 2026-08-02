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
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$")


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


def parse_diff(text: str) -> Diff:
    """Parse a unified diff into files and post-image hunk ranges.

    Only the new-side (+) numbering is tracked, because findings point at the code
    as it will exist after the merge — that is what a reviewer comments on.
    """
    diff = Diff()
    current: FileDiff | None = None
    new_line = 0

    in_hunk = False

    for raw in text.splitlines():
        # The header check must not run inside a hunk body: an *added* line whose
        # content happens to start with "++ " would otherwise be misread as a file
        # header, inventing a phantom file and silently losing the real one's line.
        m_file = _NEW_FILE_RE.match(raw)
        if not in_hunk and raw.startswith("+++ ") and m_file:
            path = m_file.group(1)
            if path == "/dev/null":  # deleted file — nothing to review
                current = None
                continue
            current = FileDiff(path=path)
            diff.files.append(current)
            continue

        if raw.startswith("diff --git") or raw.startswith("--- "):
            in_hunk = False

        if raw.startswith("@@"):
            m_hunk = _HUNK_RE.match(raw)
            if m_hunk and current is not None:
                start = int(m_hunk.group(1))
                count = int(m_hunk.group(2)) if m_hunk.group(2) is not None else 1
                # A zero-length hunk still anchors at `start`.
                end = start + max(count, 1) - 1
                current.hunks.append(Hunk(start=start, end=end))
                new_line = start
                in_hunk = True
            continue

        if current is None:
            continue

        if raw.startswith("+"):
            current.added_lines[new_line] = raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            pass  # old side only; does not advance new-side numbering
        elif raw.startswith("\\"):
            pass  # "\ No newline at end of file" — a note, not a line
        elif raw.startswith(" ") or raw == "":
            # An unmodified context line advances the new-side counter. `git diff`
            # writes a blank context line as a single space, but editors, mail
            # clients and copy-paste routinely strip trailing whitespace, leaving
            # "". Treating that as "not a line" silently shifts every subsequent
            # line number by one — findings then cite locations that are off by
            # the number of blank context lines above them.
            new_line += 1

    return diff


def render_for_prompt(diff: Diff, max_chars: int = 12000) -> str:
    """Flatten a parsed diff into the text a specialist actually sees.

    Line numbers are prefixed so the model has something concrete to cite. It is
    still checked afterwards — prompting is not enforcement.
    """
    out: list[str] = []
    for f in diff.files:
        out.append(f"--- FILE: {f.path}")
        for line_no in sorted(f.added_lines):
            out.append(f"{line_no}: {f.added_lines[line_no]}")
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… [truncated]"
    return text
