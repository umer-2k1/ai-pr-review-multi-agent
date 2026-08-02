"""Fan the diff out to every specialist, collect the lanes, aggregate.

App layer. Depends on domain (`agents`, `aggregator`) and core; only the CLI
depends on it (INV-1).

Threads rather than asyncio: each lane is a single blocking network call with no
shared state to coordinate, and a thread pool keeps the specialists themselves
free of async plumbing — which is what lets them stay testable with no network
and no event loop.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Protocol

from prreview.aggregator import aggregate
from prreview.agents.base import Specialist
from prreview.agents.docs import DocsSpecialist
from prreview.agents.quality import QualitySpecialist
from prreview.agents.security import SecuritySpecialist
from prreview.agents.tests import TestsSpecialist
from prreview.contracts import AgentResult, AgentType, Review
from prreview.diff import Diff
from prreview.llm import LLMClient

SPECIALISTS: dict[AgentType, type[Specialist]] = {
    AgentType.SECURITY: SecuritySpecialist,
    AgentType.QUALITY: QualitySpecialist,
    AgentType.TESTS: TestsSpecialist,
    AgentType.DOCS: DocsSpecialist,
}

# Wall-clock ceiling for the whole fan-out. A hung lane must not hold the review
# hostage — the study's orchestration-deadlock failure mode. Lanes that have not
# finished by then are reported as failed, not waited on.
FANOUT_TIMEOUT_S: float = 120.0


class EventSink(Protocol):
    """The seam M4's events spine plugs into."""

    def __call__(
        self, event_type: str, agent_type: AgentType | None, result: AgentResult | None
    ) -> None: ...


def build_specialists(client_factory: Callable[[AgentType], LLMClient]) -> list[Specialist]:
    return [cls(client_factory(at)) for at, cls in SPECIALISTS.items()]


def run_review(
    diff: Diff,
    client_factory: Callable[[AgentType], LLMClient],
    review_id: str | None = None,
    on_event: EventSink | None = None,
    timeout_s: float = FANOUT_TIMEOUT_S,
) -> Review:
    """Run all four specialists concurrently and aggregate the result.

    `on_event` is optional so the orchestrator stays usable — and testable —
    with no I/O at all.
    """
    rid = review_id or uuid.uuid4().hex[:12]
    specialists = build_specialists(client_factory)

    if on_event:
        on_event("review_started", None, None)

    # Raw daemon threads rather than ThreadPoolExecutor. The executor's context
    # manager calls shutdown(wait=True) on exit, so it blocks until every lane
    # finishes no matter what timeout the wait() used — the deadline is
    # decorative. `future.cancel()` cannot stop an already-running lane either,
    # and since 3.9 the executor's threads are non-daemon and joined at
    # interpreter exit, so a hung lane would hang the process on the way out.
    #
    # Daemon threads give the property actually required: the review returns on
    # the deadline, and a stuck lane cannot keep a CI job alive.
    slots: dict[AgentType, AgentResult] = {}
    lock = threading.Lock()

    def _run(specialist: Specialist) -> None:
        try:
            outcome = specialist.review(diff)
        except Exception as exc:  # noqa: BLE001 — a broken lane is a reported lane
            outcome = AgentResult(
                agent_type=specialist.agent_type,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        with lock:
            slots[specialist.agent_type] = outcome

    threads = [
        threading.Thread(target=_run, args=(s,), name=f"lane-{s.agent_type.value}", daemon=True)
        for s in specialists
    ]
    for t in threads:
        t.start()

    deadline = time.monotonic() + timeout_s
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))

    results: list[AgentResult] = []
    for specialist in specialists:
        with lock:
            outcome = slots.get(specialist.agent_type)
        if outcome is None:
            # Still running at the deadline. Report it rather than block: an
            # unfinished lane is missing evidence, and the review must say so.
            outcome = AgentResult(
                agent_type=specialist.agent_type,
                ok=False,
                error=f"TimeoutError: lane exceeded {timeout_s}s",
            )
        results.append(outcome)
        if on_event:
            on_event("agent_completed", specialist.agent_type, outcome)

    # Deterministic lane order regardless of completion order — otherwise the
    # same diff yields a differently-ordered review on every run, which makes
    # output diffing and golden tests useless.
    order = list(SPECIALISTS)
    results.sort(key=lambda r: order.index(r.agent_type))

    review = aggregate(rid, results)
    if on_event:
        on_event("review_completed", None, None)
    return review
