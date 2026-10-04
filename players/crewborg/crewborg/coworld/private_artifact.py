"""Incremental private JSONL ZIP, sealed after its owned writers join."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from time import monotonic
from typing import Literal
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from pydantic import JsonValue

from crewborg.native import NativeModel, NativeSession, PolicyGeneration

MAX_ARTIFACT_BYTES = 200 * 1024 * 1024


class PrivateOutcome(NativeModel):
    protocol: str = "crewborg.private-player.v1"
    status: Literal["completed", "truncated"]
    native_work_joined: bool
    frame_owners_joined: bool
    nlp_work_joined: bool
    socket_joined: bool
    terminal_engine_evidence: bool
    requested_player_slot: int | None
    engine_player_index: int | None
    source_revision: str | None
    image_digest: str | None
    failure_kind: str | None


class PrivateArtifact:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self.archive = ZipFile(path, "w", compression=ZIP_DEFLATED, allowZip64=True)
        self.member = self.archive.open("records.jsonl", "w", force_zip64=True)
        self.sealed = False
        self.records = 0
        self.uncompressed_bytes = 0

    def write_record(self, record: dict[str, JsonValue]) -> None:
        if self.sealed:
            raise RuntimeError("Cannot mutate sealed player artifact")
        line = json.dumps(record, separators=(",", ":"), default=str).encode() + b"\n"
        self.member.write(line)
        self.records += 1
        self.uncompressed_bytes += len(line)
        if self.path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Compressed private artifact exceeds the supported limit")

    def record_policy(self, generation: PolicyGeneration) -> None:
        self.write_record(
            {
                "kind": "native_generation"
                if generation.origin == "native"
                else "teacher_generation",
                "generation": generation.model_dump(mode="json"),
            }
        )

    def close(self) -> None:
        """The enclosing owner supplies the terminal facts before sealing."""

        if self.sealed:
            return
        self.sealed = True
        self.member.close()
        self.archive.close()
        if self.path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Compressed private artifact exceeds the supported limit")

    async def finish(
        self, outcome: PrivateOutcome, upload_url: str | None, cleanup_deadline: float
    ) -> bool:
        if not self.sealed:
            self.write_record(
                {"kind": "private_outcome", "outcome": outcome.model_dump(mode="json")}
            )
            self.close()
        if upload_url is None:
            return False

        async def chunks():
            with self.path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    yield chunk
                    await asyncio.sleep(0)

        iterator = chunks()
        destination = urlsplit(upload_url)
        if destination.scheme == "file":
            if destination.netloc:
                raise ValueError("Local player artifact requires a local file URL")
            try:
                async with asyncio.timeout_at(cleanup_deadline):
                    with Path(unquote(destination.path)).open("wb") as output:  # noqa: ASYNC230 - owned regular-volume writer; closes before return, shares the original deadline.
                        async for chunk in iterator:
                            output.write(chunk)
            finally:
                await iterator.aclose()
            return True
        client = httpx.AsyncClient(timeout=max(0.001, cleanup_deadline - monotonic()))
        try:
            async with asyncio.timeout_at(cleanup_deadline):
                response = await client.put(
                    upload_url,
                    content=iterator,
                    headers={
                        "Content-Type": "application/zip",
                        "Content-Length": str(self.path.stat().st_size),
                    },
                )
                if not response.is_success:
                    raise RuntimeError(
                        f"Private artifact upload failed: HTTP {response.status_code}"
                    )
        finally:
            await iterator.aclose()
            joined = await NativeSession.settle(client.aclose(), cleanup_deadline)
        if not joined:
            raise RuntimeError("Private artifact transport did not join")
        return True
