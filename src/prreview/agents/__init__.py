"""The specialist reasoners. Domain layer — no I/O beyond the injected LLM client.

M1 ships the security lane only; M2 adds quality, tests and docs.
"""

from prreview.agents.base import Specialist
from prreview.agents.security import SecuritySpecialist

__all__ = ["Specialist", "SecuritySpecialist"]
