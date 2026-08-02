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
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, fields
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


_EVENT_FIELDS = {f.name for f in fields(Event)}


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
        # Set when the disk sink fails. The spine then continues in memory only.
        self.write_error: str | None = None
        self._warned = False

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
        """Append one row. A failure here degrades the log, never the review.

        Observability must not be able to destroy the thing it observes. An
        earlier version let an unwritable path (a read-only working directory, a
        path whose parent is a file, `--events` pointing at a directory) escape as
        an uncaught traceback — killing a fully-computed review because a log line
        could not be written, and doing it on the *first* event, before any lane
        had run. That inverts the point of the spine.

        So: record the failure, warn once, and keep going in memory. The review
        still prints; the operator still learns the log is broken.
        """
        assert self.path is not None  # only called when path is set
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except (OSError, ValueError) as exc:
            self.write_error = f"{type(exc).__name__}: {exc}"
            if not self._warned:
                self._warned = True
                print(
                    f"warning: events spine disabled — cannot write {self.path}: {exc}",
                    file=sys.stderr,
                )

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
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                # Drop unknown keys rather than the whole row: Event promises that
                # new fields append, so a newer writer's rows must stay readable
                # by an older reader.
                known = {k: v for k, v in payload.items() if k in _EVENT_FIELDS}
                out.append(Event(**known))
            except (json.JSONDecodeError, TypeError, ValueError):
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
    # Elapsed times are measured from min(ts), not from the first row, so a row
    # arriving out of order cannot produce a negative offset.
    stamps = [e.ts for e in events]
    first = events[0]
    wall_ms = max(0, int((max(stamps) - min(stamps)) * 1000))
    lines.append(f"trace for review {first.review_id}   ({len(events)} events, {wall_ms}ms wall)")
    lines.append("")
    lines.append(f"  {'seq':>3}  {'+ms':>6}  {'event':<18} {'lane':<9} {'tok':>6}  detail")
    for e in events:
        rel = max(0, int((e.ts - min(stamps)) * 1000))
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
