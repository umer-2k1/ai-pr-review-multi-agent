"""The quality specialist — maintainability, structure, and the reliability mechanics."""

from __future__ import annotations

from prreview.agents.base import Specialist
from prreview.contracts import AgentType


class QualitySpecialist(Specialist):
    @property
    def agent_type(self) -> AgentType:
        return AgentType.QUALITY

    @property
    def concern(self) -> str:
        return """Code quality and maintainability only. Look for:
- Missing timeouts, retries or error handling on operations that can fail.
- Exceptions swallowed by bare `except` or `except Exception` with no re-raise.
- Duplicated logic that has now been copied a third time.
- Functions doing several unrelated things; unclear or misleading names.
- Resource leaks: files, sockets or connections opened without a context manager.
- Mutable default arguments, shadowed builtins, obviously dead code.
- print() where the project uses logging.

Judge by what will actually hurt someone changing this code in six months. Do NOT
report security vulnerabilities, missing tests or missing docs — other
specialists own those."""
