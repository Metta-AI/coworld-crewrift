"""Interview-mode entrypoint: a websocket SERVER that answers the commissioner.

WHY this exists (the out-of-band interview architecture)
--------------------------------------------------------
A live league player is an *outbound-only* websocket client of the game engine
(:mod:`policy_player`) with no inbound port — the commissioner cannot reach it to
chat. So league qualification runs the interview OUT OF BAND: the player
container is launched in *interview mode* (this module), which exposes a
websocket SERVER. The commissioner (or a helper it launches) connects to that
server, sends a Crewrift voting-strategy question, the player answers it via its
meeting LLM (:class:`InterviewLLMClient`), and the commissioner scores the
answer with its own LLM. This is a real Q&A decoupled from gameplay — NOT inside
a game episode and NOT a change to the Sprite-v1 game protocol.

The ``coworld.interview.v1`` JSON protocol (tiny, multi-turn-friendly)
----------------------------------------------------------------------
Text websocket messages, one JSON object per frame.

  client -> server:
    {"type": "interview_question", "question": "...", "context": {...}?, "id": "..."?}
    {"type": "done"}                # optional explicit end; closing the socket also ends it
  server -> client:
    {"type": "interview_ready", "protocol": "coworld.interview.v1", "llm_enabled": bool}
    {"type": "interview_answer", "answer": "...", "degraded": bool, "model": "..."?, "id": "..."?}
    {"type": "error", "error": "..."}   # malformed frame; the loop continues

The server loops until the client sends ``done`` or closes the socket. The
``id`` is echoed back if the client supplies one (so a multi-turn client can
correlate answers). On the first connection it sends ``interview_ready`` so the
commissioner can confirm liveness and whether a real LLM is wired.

Environment
-----------
- ``CREWRIFT_INTERVIEW_PORT``  — TCP port to listen on (default ``8770``).
- ``CREWRIFT_INTERVIEW_HOST``  — bind host (default ``0.0.0.0`` so the platform
  can reach the container).
- Plus all the meeting-LLM env flags consumed by :func:`read_meeting_params_from_env`
  (``CREWBOT3000_LLM_MEETINGS`` / Bedrock flags / ``ANTHROPIC_API_KEY`` /
  ``CREWBOT3000_LLM_MODEL`` / ...), and the interview overrides
  ``CREWRIFT_INTERVIEW_MAX_TOKENS`` / ``CREWRIFT_INTERVIEW_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Awaitable, Callable

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from players.crewrift.crewbot3000.strategy.meeting.interview import InterviewLLMClient

PROTOCOL_VERSION = "coworld.interview.v1"
DEFAULT_INTERVIEW_PORT = 8770
DEFAULT_INTERVIEW_HOST = "0.0.0.0"

# Message type tags (the coworld.interview.v1 contract).
MSG_QUESTION = "interview_question"
MSG_ANSWER = "interview_answer"
MSG_READY = "interview_ready"
MSG_DONE = "done"
MSG_ERROR = "error"


def _answer_for(client: InterviewLLMClient, question: str, context: dict[str, Any] | None) -> dict[str, Any]:
    """Run one interview answer and shape it into the wire payload (pure-ish)."""
    result = client.answer(question, context)
    payload: dict[str, Any] = {
        "type": MSG_ANSWER,
        "answer": result.answer,
        "degraded": result.degraded,
    }
    if result.model is not None:
        payload["model"] = result.model
    return payload


async def _serve_connection(websocket: Any, client: InterviewLLMClient) -> None:
    """Handle one commissioner connection: ready -> Q&A loop until done/close."""
    await websocket.send(
        json.dumps(
            {"type": MSG_READY, "protocol": PROTOCOL_VERSION, "llm_enabled": client.enabled}
        )
    )
    try:
        async for raw in websocket:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": MSG_ERROR, "error": "invalid JSON frame"}))
                continue
            if not isinstance(message, dict):
                await websocket.send(json.dumps({"type": MSG_ERROR, "error": "frame must be a JSON object"}))
                continue
            msg_type = message.get("type")
            if msg_type == MSG_DONE:
                return
            if msg_type != MSG_QUESTION:
                await websocket.send(
                    json.dumps({"type": MSG_ERROR, "error": f"unexpected message type {msg_type!r}"})
                )
                continue
            question = message.get("question")
            if not isinstance(question, str) or not question.strip():
                await websocket.send(
                    json.dumps({"type": MSG_ERROR, "error": "interview_question requires a non-empty 'question'"})
                )
                continue
            context = message.get("context") if isinstance(message.get("context"), dict) else None
            # The LLM call is blocking; run it off the event loop so a slow model
            # never stalls the websocket heartbeat.
            payload = await asyncio.to_thread(_answer_for, client, question, context)
            if message.get("id") is not None:
                payload["id"] = message.get("id")
            await websocket.send(json.dumps(payload))
    except ConnectionClosed:
        # The commissioner finished and dropped the socket — a normal end.
        return


async def run_interview_server(
    *,
    host: str = DEFAULT_INTERVIEW_HOST,
    port: int = DEFAULT_INTERVIEW_PORT,
    client: InterviewLLMClient | None = None,
    serve_factory: Callable[..., Any] = serve,
    ready_event: asyncio.Event | None = None,
    stop: Awaitable[None] | None = None,
) -> None:
    """Start the interview websocket server and serve until ``stop`` resolves.

    ``client`` is injectable for tests; by default it is built from the
    environment (:meth:`InterviewLLMClient.from_env`). ``ready_event`` (if given)
    is set once the server is accepting connections. ``stop`` (if given) is
    awaited to trigger graceful shutdown; otherwise the server runs forever.
    """
    interview_client = client or InterviewLLMClient.from_env()

    async def handler(websocket: Any) -> None:
        await _serve_connection(websocket, interview_client)

    async with serve_factory(handler, host, port) as server:
        bound = server.sockets[0].getsockname() if getattr(server, "sockets", None) else (host, port)
        print(
            f"crewbot3000 interview server listening on {bound} "
            f"(protocol {PROTOCOL_VERSION}, llm_enabled={interview_client.enabled})",
            file=sys.stderr,
            flush=True,
        )
        if ready_event is not None:
            ready_event.set()
        if stop is not None:
            await stop
        else:
            await asyncio.Future()  # run forever


def _port_from_env() -> int:
    try:
        return int(os.environ.get("CREWRIFT_INTERVIEW_PORT", DEFAULT_INTERVIEW_PORT))
    except (TypeError, ValueError):
        return DEFAULT_INTERVIEW_PORT


def main() -> None:
    host = os.environ.get("CREWRIFT_INTERVIEW_HOST", DEFAULT_INTERVIEW_HOST)
    port = _port_from_env()
    asyncio.run(run_interview_server(host=host, port=port))


if __name__ == "__main__":
    main()
