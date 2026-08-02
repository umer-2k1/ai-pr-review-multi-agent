"""The events spine — one time-ordered row per action.

App layer.

The study's argument, kept intact at prototype scale: every action a review takes
becomes one append-only, time-ordered row, and that single stream then answers
three different questions — what happened (trace), who did what (audit), and what
it cost (economics). Three questions, one write path.

What is deliberately NOT here: Postgres, hypertables, continuous aggregates,
Tiger. The idea that earns its place is the append-only ordered log; JSONL on
disk satisfies it at this scale. The graduation path to a real hypertable is a
change of sink, not a change of design — which is why `EventSink` is a protocol
and the row shape is fixed here rather than at the storage layer.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from prreview.contracts import AgentResult, AgentType

DEFAULT_EVENTS_DIR = Path(".events")


@dataclass(frozen=True)
class Event:
    """One row. Field order is the row order; new fields append, never reorder."""

    review_id: str
    seq: int
    event_type: str
    ts: float
    agent_type: str | None = None
    ok: bool | None = None
    error: str | None = None
    tokens: int = 0
    duration_ms: int = 0
    findings: int = 0
    dropped_ungrounded: int = 0
    dropped_malformed: int = 0
    detail: dict[str, object] = field(default_factory=dict)


class EventLog:
    """An append-only, monotonically-ordered event log for one process.

    `seq` is assigned under a lock rather than derived from the timestamp. Four
    specialist lanes complete concurrently and a wall clock can repeat or go
    backwards (NTP, suspend); the whole value of the spine is that the order is
    reconstructable, so ordering cannot depend on the clock.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._events: list[Event] = []

    def emit(
        self,
        review_id: str,
        event_type: str,
        agent_type: AgentType | None = None,
        result: AgentResult | None = None,
        **detail: object,
    ) -> Event:
        with self._lock:
            self._seq += 1
            seq = self._seq
            event = Event(
                review_id=review_id,
                seq=seq,
                event_type=event_type,
                ts=time.time(),
                agent_type=agent_type.value if agent_type else None,
                ok=result.ok if result else None,
                error=result.error if result else None,
                tokens=result.tokens if result else 0,
                duration_ms=result.duration_ms if result else 0,
                findings=len(result.findings) if result else 0,
                dropped_ungrounded=result.dropped_ungrounded if result else 0,
                dropped_malformed=result.dropped_malformed if result else 0,
                detail=dict(detail),
            )
            self._events.append(event)
            if self.path is not None:
                self._append_to_disk(event)
            return event

    def _append_to_disk(self, event: Event) -> None:
        assert self.path is not None  # only called when path is set
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    @property
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def total_tokens(self) -> int:
        return sum(e.tokens for e in self.events)

    def total_duration_ms(self) -> int:
        return sum(e.duration_ms for e in self.events)


def read_log(path: Path) -> list[Event]:
    """Read a JSONL log back. Skips malformed rows rather than failing the read.

    A corrupt row must not make the whole trace unreadable — the log exists to be
    consulted when something has already gone wrong.
    """
    out: list[Event] = []
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Event(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
    return out


def latest_review_id(path: Path) -> str | None:
    events = read_log(path)
    return events[-1].review_id if events else None


def format_trace(events: list[Event]) -> str:
    """Human-readable reconstruction of one review from its rows."""
    if not events:
        return "no events recorded."
    lines: list[str] = []
    first, last = events[0], events[-1]
    wall_ms = int((last.ts - first.ts) * 1000)
    lines.append(f"trace for review {first.review_id}   ({len(events)} events, {wall_ms}ms wall)")
    lines.append("")
    lines.append(f"  {'seq':>3}  {'+ms':>6}  {'event':<18} {'lane':<9} {'tok':>6}  detail")
    for e in events:
        rel = int((e.ts - first.ts) * 1000)
        bits: list[str] = []
        if e.ok is False:
            bits.append(f"FAILED: {e.error}")
        elif e.event_type == "agent_completed":
            bits.append(f"{e.findings} finding(s)")
            if e.dropped_ungrounded:
                bits.append(f"{e.dropped_ungrounded} ungrounded")
            if e.dropped_malformed:
                bits.append(f"{e.dropped_malformed} malformed")
        for k, v in e.detail.items():
            bits.append(f"{k}={v}")
        lines.append(
            f"  {e.seq:>3}  {rel:>6}  {e.event_type:<18} {(e.agent_type or '-'):<9} "
            f"{e.tokens:>6}  {', '.join(bits)}"
        )
    lines.append("")
    lines.append(f"  total tokens: {sum(e.tokens for e in events)}")
    return "\n".join(lines)
