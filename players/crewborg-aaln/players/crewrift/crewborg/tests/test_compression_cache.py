"""Standard ZIP recovery and ownership for immutable runtime compression."""

import hashlib
import threading
import time
import zipfile
import zlib

import pytest

from players.crewrift.crewborg.compression_cache import (
    CHUNK_BYTES,
    ImmutableCompressionCache,
)


def test_final_actual_bytes_override_stale_cache(tmp_path):
    source = tmp_path / "trace.db"
    original = b"a" * CHUNK_BYTES + b"b" * CHUNK_BYTES + b"tail"
    source.write_bytes(original)
    lock = threading.Lock()
    cache = ImmutableCompressionCache(source, lock)
    expected_keys = {
        hashlib.sha256(chunk).hexdigest()
        for chunk in (
            original[:CHUNK_BYTES],
            original[CHUNK_BYTES : 2 * CHUNK_BYTES],
            original[2 * CHUNK_BYTES :],
        )
    }
    deadline = time.monotonic() + 2
    while not expected_keys.issubset(cache.chunks) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert expected_keys.issubset(cache.chunks)
    final = b"c" * CHUNK_BYTES + original[CHUNK_BYTES:] + b"new"
    with lock:
        source.write_bytes(final)
    assert cache.stop(deadline)
    path = tmp_path / "player.zip"
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        compressor = cache.write_zip(archive, "trace.db", time.monotonic() + 2)
    assert compressor.source_complete
    assert compressor.cache_hits == 1
    assert compressor.cache_misses == 2
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("trace.db")
        assert archive.read("trace.db") == final
        assert info.CRC == zlib.crc32(final)
        assert info.file_size == len(final)
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.extract_version == 45
        assert archive.testzip() is None


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_cache_damage_never_becomes_success(tmp_path, damage):
    source = tmp_path / "trace.db"
    raw = b"private" * 100
    source.write_bytes(raw)
    cache = ImmutableCompressionCache(source, threading.Lock())
    key = hashlib.sha256(raw).hexdigest()
    deadline = time.monotonic() + 2
    while key not in cache.chunks and time.monotonic() < deadline:
        time.sleep(0.001)
    assert cache.stop(deadline)
    chunk = cache.chunks[key]
    if damage == "corrupt":
        chunk.path.write_bytes(b"wrong")
        error = ValueError
    else:
        chunk.path.unlink()
        error = FileNotFoundError
    with pytest.raises(error):
        cache.compressor().compress(raw)


def test_pending_reader_cannot_be_used_before_join(tmp_path):
    source = tmp_path / "trace.db"
    source.write_bytes(b"actual")
    lock = threading.Lock()
    entered = threading.Event()

    class OwnedLock:
        def __enter__(self):
            entered.set()
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    lock.acquire()
    cache = ImmutableCompressionCache(source, OwnedLock())
    assert entered.wait(2)
    try:
        assert not cache.stop(time.monotonic() - 1)
        with pytest.raises(RuntimeError, match="not joined"):
            cache.compressor()
    finally:
        lock.release()
        assert cache.stop(time.monotonic() + 2)


def test_worker_failure_is_retained_after_join(tmp_path):
    source = tmp_path / "absent.db"
    cache = ImmutableCompressionCache(source, threading.Lock())
    with pytest.raises(FileNotFoundError):
        cache.operation.result(timeout=2)
    with pytest.raises(FileNotFoundError):
        cache.stop(time.monotonic() + 2)
    assert cache.joined


def test_expired_assembly_never_reads_or_claims_complete_source(tmp_path):
    source = tmp_path / "trace.db"
    source.write_bytes(b"actual final bytes")
    cache = ImmutableCompressionCache(source, threading.Lock())
    assert cache.stop(time.monotonic() + 2)
    path = tmp_path / "player.zip"
    with zipfile.ZipFile(path, "w") as archive:
        compressor = cache.write_zip(archive, "trace.db", time.monotonic() - 1)
    assert not compressor.source_complete
    assert compressor.cache_hits == compressor.cache_misses == 0
    with zipfile.ZipFile(path) as archive:
        assert archive.read("trace.db") == b""
