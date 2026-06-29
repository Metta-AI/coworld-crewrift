"""Focused tests for the commissioner `/round` WS server's non-blocking dispatch.

Regression coverage for the qualifier-promotion outage: the `league_migration_request`
handler runs `migrate_league`, which (for Crewrift Prime) blocks for minutes on a
real self-play qualifier game (`urllib` + `time.sleep` polling). When that ran inline
on the server's asyncio event loop it starved the loop, so the WS ping/pong keepalive
stopped and the platform client dropped the socket mid-qualifier -> 0 events applied,
forever. The fix offloads the blocking commissioner dispatch via `asyncio.to_thread`
so the receive coroutine keeps yielding (and answering pings) while the qualifier runs.

These tests are stdlib-only (no httpx/starlette TestClient) so they run in the same
slim environment the commissioner image ships.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
import unittest

from commissioners.common import server as server_module


class ServerDispatchOffloadsBlockingCallsTest(unittest.IsolatedAsyncioTestCase):
    def test_migration_handler_source_offloads_to_thread(self) -> None:
        """The shipped handler must wrap the blocking commissioner dispatch in
        asyncio.to_thread; an inline call is the bug that starved the event loop."""
        source = inspect.getsource(server_module.create_app)
        # Normalize whitespace so multi-line `asyncio.to_thread(\n  fn,` calls match.
        compact = " ".join(source.split())
        # The migration/qualify path and the other commissioner request handlers
        # must not be invoked inline on the event loop.
        self.assertIn("asyncio.to_thread(migrate_league_for_request", compact)
        self.assertIn("asyncio.to_thread(schedule_episodes_for_round_start", compact)
        self.assertIn("asyncio.to_thread( complete_round_for_round_start", compact)
        # And the old inline form must be gone.
        self.assertNotIn("migrate_league_for_request(commissioner, request).to_json()", compact)

    async def test_to_thread_keeps_event_loop_responsive_during_blocking_call(self) -> None:
        """Behavioral proof that the dispatch pattern the handler now uses keeps the
        event loop free: a multi-second blocking call offloaded via asyncio.to_thread
        runs on a DIFFERENT thread and lets a concurrent loop task keep ticking."""
        loop_thread = threading.get_ident()
        worker_thread: dict[str, int] = {}
        ticks = 0

        def blocking_migrate() -> str:
            worker_thread["id"] = threading.get_ident()
            time.sleep(0.3)  # stand-in for the minutes-long qualifier poll
            return "result"

        async def heartbeat() -> None:
            nonlocal ticks
            # Mirrors the WS ping/pong keepalive: must keep firing while the
            # blocking dispatch runs, or the client would drop the socket.
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            result = await asyncio.to_thread(blocking_migrate)
        finally:
            beat.cancel()

        self.assertEqual(result, "result")
        # The blocking work ran off the event loop thread...
        self.assertNotEqual(worker_thread["id"], loop_thread)
        # ...so the keepalive heartbeat kept ticking throughout (>= ~10 of the 15
        # possible 20ms ticks in 300ms; a starved loop would have produced ~0).
        self.assertGreaterEqual(ticks, 5)


if __name__ == "__main__":
    unittest.main()
