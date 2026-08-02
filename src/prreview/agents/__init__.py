"""The four specialist reasoners. Domain layer — no I/O beyond the injected LLM client."""

from prreview.agents.base import Specialist
from prreview.agents.docs import DocsSpecialist
from prreview.agents.quality import QualitySpecialist
from prreview.agents.security import SecuritySpecialist
from prreview.agents.tests import TestsSpecialist

__all__ = [
    "Specialist",
    "SecuritySpecialist",
    "QualitySpecialist",
    "TestsSpecialist",
    "DocsSpecialist",
]
