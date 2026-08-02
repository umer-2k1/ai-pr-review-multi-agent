"""The events spine: one time-ordered row per action.

The study's claim, kept intact at prototype scale: a single append-only stream
answers three different questions — what happened (trace), who did what (audit),
and what it cost (economics). These tests pin the properties that make that true:
ordering that does not depend on the wall clock, one row per action, and a log
that stays readable when a row is corrupt.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from prreview.contracts import AgentResult, AgentType
from prreview.diff import parse_diff
from prreview.events import Event, EventLog, format_trace, latest_review_id, read_log
from prreview.llm import FailingLLM, LLMClient, OfflineLLM
from prreview.orchestrator import run_review

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.diff"


def _diff():  # type: ignore[no-untyped-def]
    with FIXTURE.open("r", newline="") as fh:
        return parse_diff(fh.read())


def _offline(at: AgentType) -> LLMClient:
    return OfflineLLM(at.value)


class TestOrdering:
    def test_seq_is_monotonic(self) -> None:
        log = EventLog()
        for i in range(50):
            log.emit("r", "tick")
        assert [e.seq for e in log.events] == list(range(1, 51))

    def test_seq_is_assigned_under_a_lock_not_derived_from_the_clock(self) -> None:
        """Four lanes complete concurrently and a wall clock can repeat or go
        backwards (NTP, suspend). The whole value of the spine is that the order
        is reconstructable, so ordering must not depend on the clock."""
        log = EventLog()

        def hammer() -> None:
            for _ in range(100):
                log.emit("r", "tick")

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        seqs = sorted(e.seq for e in log.events)
        assert seqs == list(range(1, 801)), "concurrent emits lost or duplicated a sequence number"

    def test_on_disk_order_matches_seq_order_under_concurrency(self, tmp_path: Path) -> None:
        """The property design decision #1 exists to guarantee.

        The disk append sits inside the lock, so file order == seq order. Moving
        it outside still produced a correct in-memory sequence — so this needs
        its own test: `trace` renders rows in the order it reads them, and a
        divergence would render an out-of-order trace.
        """
        path = tmp_path / "events.jsonl"
        log = EventLog(path=path)

        def hammer() -> None:
            for _ in range(60):
                log.emit("r", "tick")

        threads = [threading.Thread(target=hammer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        disk_seqs = [json.loads(line)["seq"] for line in path.read_text().splitlines() if line]
        assert len(disk_seqs) == 360
        assert disk_seqs == sorted(disk_seqs), "on-disk order diverged from seq order"

    def test_concurrent_emits_do_not_lose_rows(self) -> None:
        log = EventLog()
        threads = [
            threading.Thread(target=lambda: [log.emit("r", "e") for _ in range(50)])
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(log.events) == 200


class TestRowShape:
    def test_an_agent_row_carries_the_cost_and_outcome_fields(self) -> None:
        log = EventLog()
        result = AgentResult(
            agent_type=AgentType.SECURITY, ok=True, tokens=123, duration_ms=45,
            dropped_ungrounded=2, dropped_malformed=1,
        )
        event = log.emit("rev1", "agent_completed", AgentType.SECURITY, result)

        assert event.review_id == "rev1"
        assert event.event_type == "agent_completed"
        assert event.agent_type == "security"
        assert event.tokens == 123
        assert event.duration_ms == 45
        assert event.dropped_ungrounded == 2
        assert event.dropped_malformed == 1

    def test_a_failed_lane_records_its_error(self) -> None:
        log = EventLog()
        event = log.emit("r", "agent_completed", AgentType.DOCS,
                         AgentResult(agent_type=AgentType.DOCS, ok=False, error="boom"))
        assert event.ok is False
        assert event.error == "boom"

    def test_arbitrary_detail_is_carried(self) -> None:
        log = EventLog()
        event = log.emit("r", "gate_decided", None, None, verdict="HOLD", confidence=0.5)
        assert event.detail == {"verdict": "HOLD", "confidence": 0.5}


class TestPersistence:
    def test_rows_round_trip_through_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path=path)
        log.emit("r1", "review_started")
        log.emit("r1", "agent_completed", AgentType.TESTS,
                 AgentResult(agent_type=AgentType.TESTS, tokens=7))

        back = read_log(path)
        assert [e.seq for e in back] == [1, 2]
        assert back[1].tokens == 7
        assert back[1].agent_type == "tests"

    def test_the_log_is_append_only_across_runs(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        EventLog(path=path).emit("r1", "review_started")
        EventLog(path=path).emit("r2", "review_started")
        assert len(read_log(path)) == 2, "a later run truncated the log"

    def test_a_corrupt_row_does_not_make_the_trace_unreadable(self, tmp_path: Path) -> None:
        """The log exists to be consulted when something has already gone wrong."""
        path = tmp_path / "events.jsonl"
        log = EventLog(path=path)
        log.emit("r1", "review_started")
        with path.open("a") as fh:
            fh.write("{not json at all\n")
            fh.write('{"unexpected": "shape"}\n')
        log.emit("r1", "review_completed")

        back = read_log(path)
        assert len(back) == 2, "good rows were lost because of a bad one"
        assert [e.event_type for e in back] == ["review_started", "review_completed"]

    def test_reading_a_missing_log_is_not_an_error(self, tmp_path: Path) -> None:
        assert read_log(tmp_path / "nope.jsonl") == []
        assert latest_review_id(tmp_path / "nope.jsonl") is None

    def test_latest_review_id_finds_the_most_recent(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path=path)
        log.emit("old", "review_started")
        log.emit("new", "review_started")
        assert latest_review_id(path) == "new"

    def test_no_path_means_no_file_is_written(self, tmp_path: Path) -> None:
        log = EventLog(path=None)
        log.emit("r", "e")
        assert list(tmp_path.iterdir()) == []
        assert len(log.events) == 1

    def test_unicode_survives_the_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        EventLog(path=path).emit("r", "e", None, None, note="weiß · 日本語 · 🔒")
        assert read_log(path)[0].detail["note"] == "weiß · 日本語 · 🔒"


class TestEndToEnd:
    def test_a_full_review_emits_a_row_per_action(self) -> None:
        log = EventLog()

        def sink(event_type, agent_type, result):  # type: ignore[no-untyped-def]
            log.emit("rev", event_type, agent_type, result)

        run_review(_diff(), _offline, review_id="rev", on_event=sink)

        kinds = [e.event_type for e in log.events]
        assert kinds[0] == "review_started"
        assert kinds[-1] == "review_completed"
        assert kinds.count("agent_completed") == 4
        assert len(log.events) >= 6

    def test_the_spine_prices_the_review(self) -> None:
        log = EventLog()

        def sink(event_type, agent_type, result):  # type: ignore[no-untyped-def]
            log.emit("rev", event_type, agent_type, result)

        run_review(_diff(), _offline, review_id="rev", on_event=sink)
        assert log.total_tokens() > 0, "the same rows must answer the cost question"

    def test_a_failed_lane_is_visible_in_the_trace(self) -> None:
        """Audit: the spine must show that a lane died, not just that the review
        finished."""
        log = EventLog()

        def sink(event_type, agent_type, result):  # type: ignore[no-untyped-def]
            log.emit("rev", event_type, agent_type, result)

        def factory(at: AgentType) -> LLMClient:
            return FailingLLM() if at is AgentType.QUALITY else OfflineLLM(at.value)

        run_review(_diff(), factory, review_id="rev", on_event=sink)

        failed = [e for e in log.events if e.ok is False]
        assert len(failed) == 1
        assert failed[0].agent_type == "quality"

    def test_trace_reconstructs_one_review_only(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path=path)
        log.emit("aaa", "review_started")
        log.emit("bbb", "review_started")
        log.emit("aaa", "review_completed")

        rows = [e for e in read_log(path) if e.review_id == "aaa"]
        assert len(rows) == 2
        assert "aaa" in format_trace(rows)
        assert "bbb" not in format_trace(rows)

    def test_format_trace_on_no_events(self) -> None:
        assert "no events" in format_trace([])
