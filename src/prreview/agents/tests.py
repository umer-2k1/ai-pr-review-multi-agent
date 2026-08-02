"""The tests specialist — is the new behaviour actually pinned down?"""

from __future__ import annotations

from prreview.agents.base import Specialist
from prreview.contracts import AgentType


class TestsSpecialist(Specialist):
    @property
    def agent_type(self) -> AgentType:
        return AgentType.TESTS

    @property
    def concern(self) -> str:
        return """Test coverage and test quality only. Look for:
- New branches, error paths or edge cases with no corresponding test.
- Tests that assert nothing meaningful (assert True, assert result, no assertion).
- Tests that restate the implementation instead of pinning behaviour.
- Changed behaviour whose existing test was updated to match rather than to verify.
- Missing negative cases: what happens on bad input, empty input, failure?
- Unresolved TODO/FIXME markers on code paths that ship untested.

An untested error path is the one that fires at 3am. Do NOT report security
issues, code style or documentation — other specialists own those."""
