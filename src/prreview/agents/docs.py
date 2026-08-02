"""The docs specialist — will the next person understand why, not just what?"""

from __future__ import annotations

from prreview.agents.base import Specialist
from prreview.contracts import AgentType


class DocsSpecialist(Specialist):
    @property
    def agent_type(self) -> AgentType:
        return AgentType.DOCS

    @property
    def concern(self) -> str:
        return """Documentation only. Look for:
- New public functions, classes or endpoints with no docstring.
- Docstrings or comments that now contradict the code they describe.
- Non-obvious decisions with no comment explaining WHY (the what is readable already).
- Changed configuration, env vars or CLI flags not reflected in README/docs.
- Unresolved TODO/FIXME markers left as the only explanation of a decision.
- Breaking changes with no migration note.

Comments that restate the code are noise; flag missing rationale, not missing
narration. Do NOT report security issues, code structure or test gaps — other
specialists own those."""
