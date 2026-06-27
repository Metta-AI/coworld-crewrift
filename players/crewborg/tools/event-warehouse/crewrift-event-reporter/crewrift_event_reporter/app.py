from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, WebSocket

from .protocol import ReportFailed, ReportFinished, ReporterReady, ReportRequest, ReportStarted
from .service import build_and_write_report

app = FastAPI(title="Crewrift Event Reporter")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/reporter")
async def reporter(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(ReporterReady().model_dump())
    raw: dict[str, Any] | None = None
    request_id = "unknown"
    try:
        raw = await websocket.receive_json()
        request = ReportRequest.model_validate(raw)
        request_id = request.request_id
        await websocket.send_json(ReportStarted(request_id=request.request_id, episode_count=1).model_dump())
        result = await asyncio.to_thread(build_and_write_report, request)
        await websocket.send_json(
            ReportFinished(
                request_id=request.request_id,
                report_uri=request.report_uri,
                episode_count=1,
                players=result["players"],
            ).model_dump()
        )
    except Exception as exc:
        if raw is not None and isinstance(raw, dict):
            request_id = str(raw.get("request_id", request_id))
        await websocket.send_json(
            ReportFailed(request_id=request_id, stage="report", error=f"{type(exc).__name__}: {exc}").model_dump()
        )
    finally:
        await websocket.close()
