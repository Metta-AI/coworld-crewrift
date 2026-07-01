from __future__ import annotations

import threading

from crewborg.strategy.commander.trace import CommanderTrace


def test_commander_trace_records_and_drains_events() -> None:
    trace = CommanderTrace()

    trace.record("commander_started", {"enabled": True})
    trace.record("commander_call", {"outcome": "ok"})

    assert trace.drain() == [
        ("commander_started", {"enabled": True}),
        ("commander_call", {"outcome": "ok"}),
    ]
    assert trace.drain() == []


def test_commander_trace_is_bounded_and_reports_dropped_records() -> None:
    trace = CommanderTrace(capacity=2)

    trace.record("commander_call_start", {"call": 1})
    trace.record("commander_call_start", {"call": 2})
    trace.record("commander_call_start", {"call": 3})

    assert trace.drain() == [
        ("commander_trace_dropped", {"dropped": 1}),
        ("commander_call_start", {"call": 2}),
        ("commander_call_start", {"call": 3}),
    ]
    assert trace.drain() == []


def test_commander_trace_allows_cross_thread_recording() -> None:
    trace = CommanderTrace()

    def record(index: int) -> None:
        trace.record("commander_call_start", {"index": index})

    threads = [threading.Thread(target=record, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    drained = trace.drain()
    assert len(drained) == 8
    assert {data["index"] for event, data in drained if event == "commander_call_start"} == set(range(8))
