"""One owned commander call; shutdown prevents late publication and retry admission."""

from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from crewborg.native import NativeRequest
from crewborg.strategy.commander.llm import CommanderLLMResult
from crewborg.strategy.commander.trace import CommanderTrace
from crewborg.strategy.commander.worker import CommanderWorker


@pytest.mark.asyncio
async def test_commander_closes_and_joins_its_pending_call_without_late_publication(
    native_session,
):
    started = asyncio.Event()

    class Client:
        enabled = True
        disabled_reason = None
        calls = 0

        def decide(self, context):
            self.calls += 1
            prepared = native_session.prepare(
                NativeRequest(
                    model="fixture", messages=[], max_tokens=1, temperature=0
                ),
                phase="commander",
                observation_tick=context["observation_tick"],
                deadline=monotonic() + 10,
            )

            async def run():
                started.set()
                await asyncio.sleep(10)
                return CommanderLLMResult(
                    priorities={"reason": "late"}, generation=prepared.generation
                )

            return native_session.start(prepared, run())

    client = Client()
    trace = CommanderTrace()
    worker = CommanderWorker(lambda: client, session=native_session, trace=trace)
    worker.start()
    worker.snapshots.publish({"observation_tick": 1})
    worker.poll()
    await started.wait()
    worker.snapshots.publish({"observation_tick": 2})
    worker.poll()
    assert client.calls == 1
    worker.close()
    deadline = native_session.shutdown_deadline
    assert deadline is not None
    assert await native_session.stop(deadline)
    worker.poll()
    assert worker.priorities.take() is None
    assert client.calls == 1
    assert len(native_session.generations) == 1
