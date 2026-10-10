"""Owned immutable DEFLATE chunks, revalidated against the closed SQLite file."""

from __future__ import annotations

import hashlib
import threading
import zlib
from _thread import LockType
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Protocol, cast
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict

CHUNK_BYTES = 256 * 1024


class CachedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    raw_sha256: str
    raw_bytes: int
    compressed_sha256: str
    compressed_bytes: int
    path: Path


class ImmutableCompressionCache:
    """One owner copies file chunks under the recorder lock, then compresses copies."""

    def __init__(self, source: Path, source_lock: LockType):
        self.source = source
        self.source_lock = source_lock
        self.directory = source.with_suffix(".deflate-chunks")
        self.directory.mkdir(mode=0o700)
        self.chunks: dict[str, CachedChunk] = {}
        self.stop_requested = threading.Event()
        self.joined = False
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sqlite-compression"
        )
        self.operation = self.executor.submit(self._run)

    def _run(self) -> None:
        while not self.stop_requested.is_set():
            with self.source.open("rb") as reader:
                while not self.stop_requested.is_set():
                    with self.source_lock:
                        raw = reader.read(CHUNK_BYTES)
                    if not raw:
                        break
                    key = hashlib.sha256(raw).hexdigest()
                    if key in self.chunks:
                        continue
                    compressor = zlib.compressobj(level=1, wbits=-15)
                    compressed = compressor.compress(raw) + compressor.flush(
                        zlib.Z_FULL_FLUSH
                    )
                    path = self.directory / (key + ".deflate")
                    with path.open("xb") as writer:
                        path.chmod(0o600)
                        writer.write(compressed)
                    self.chunks[key] = CachedChunk(
                        raw_sha256=key,
                        raw_bytes=len(raw),
                        compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                        compressed_bytes=len(compressed),
                        path=path,
                    )
            self.stop_requested.wait(10)

    def stop(self, deadline: float) -> bool:
        self.stop_requested.set()
        self.executor.shutdown(wait=False)
        for thread in self.executor._threads:
            thread.join(max(0, deadline - monotonic()))
        self.joined = self.operation.done() and all(
            not thread.is_alive() for thread in self.executor._threads
        )
        if self.joined:
            self.operation.result()
        return self.joined

    def compressor(self) -> CachedDeflater:
        if not self.joined:
            raise RuntimeError("Compression owner has not joined")
        self.operation.result()
        return CachedDeflater(self.chunks)

    def write_zip(self, archive: ZipFile, name: str, deadline: float) -> CachedDeflater:
        """Use the standard ZIP writer's CRC/size accounting with cached raw blocks."""
        compressor = self.compressor()
        info = ZipInfo.from_file(self.source, name)
        info.compress_type = ZIP_DEFLATED
        with archive.open(info, "w", force_zip64=True) as member:
            writer = cast(DeflatedZipMember, member)
            writer._compressor = compressor
            with self.source.open("rb") as reader:
                while monotonic() < deadline:
                    raw = reader.read(CHUNK_BYTES)
                    if not raw:
                        compressor.source_complete = True
                        break
                    writer.write(raw)
        return compressor


class DeflatedZipMember(Protocol):
    # CPython's standard _ZipWriteFile owns CRC, size, ZIP64 and final flush.
    _compressor: CachedDeflater

    def write(self, data: bytes) -> int: ...


class CachedDeflater:
    """A standard raw-DEFLATE stream projected from actual final file bytes."""

    def __init__(self, chunks: dict[str, CachedChunk]):
        self.chunks = dict(chunks)
        self.finished = False
        self.source_complete = False
        self.cache_hits = 0
        self.cache_misses = 0

    def compress(self, raw: bytes) -> bytes:
        if self.finished:
            raise RuntimeError("Cannot append to sealed DEFLATE evidence")
        key = hashlib.sha256(raw).hexdigest()
        if key in self.chunks:
            chunk = self.chunks[key]
            compressed = chunk.path.read_bytes()
            if (
                key != chunk.raw_sha256
                or len(raw) != chunk.raw_bytes
                or len(compressed) != chunk.compressed_bytes
                or hashlib.sha256(compressed).hexdigest() != chunk.compressed_sha256
            ):
                raise ValueError("Immutable compression cache integrity failed")
            self.cache_hits += 1
            return compressed
        self.cache_misses += 1
        compressor = zlib.compressobj(level=1, wbits=-15)
        return compressor.compress(raw) + compressor.flush(zlib.Z_FULL_FLUSH)

    def flush(self) -> bytes:
        if self.finished:
            raise RuntimeError("DEFLATE evidence already sealed")
        self.finished = True
        return zlib.compressobj(level=1, wbits=-15).flush(zlib.Z_FINISH)
