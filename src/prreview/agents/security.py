"""The security specialist — the lane whose misses are most expensive.

Study §0.3: "a wrong style comment is annoying; a missed SQL injection is
dangerous." That asymmetry is why this lane is the one M1 builds first.
"""

from __future__ import annotations

from prreview.agents.base import Specialist
from prreview.contracts import AgentType


class SecuritySpecialist(Specialist):
    @property
    def agent_type(self) -> AgentType:
        return AgentType.SECURITY

    @property
    def concern(self) -> str:
        return """Security only. Look for:
- Injection: SQL/command/template built from interpolated or concatenated input.
- Secrets: credentials, tokens or keys committed as literals.
- Unsafe execution: eval/exec/pickle/yaml.load over data an attacker can influence.
- AuthN/AuthZ: a permission check removed, weakened, or missing on a new path.
- Unsafe deserialization, path traversal, SSRF, unvalidated redirects.
- Crypto misuse: fixed IVs, ECB mode, home-rolled primitives, weak hashes for passwords.

Judge severity by blast radius, not by how unusual the pattern looks. Reserve
`critical` for something exploitable as written. Do NOT report style, naming,
formatting, missing tests or missing docs — other specialists own those, and
duplicating them wastes the reviewer's attention."""
