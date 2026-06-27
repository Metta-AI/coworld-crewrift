from __future__ import annotations

import json
import zlib
from typing import Any

from .protocol import ReporterArtifactRef, ReporterEpisodeInput, ReporterEpisodeManifest
from .uri_io import read_uri


class BundleReader:
    """Reads one episode's artifacts through the same token accessors the
    old zip-backed reader exposed, but fetches presigned refs lazily.

    Replaces the prior zip-download reader (PR #15877): the backend now hands
    the reporter presigned GET URLs per artifact instead of a relayed zip, so
    each token is fetched on demand directly from the eval bucket and zlib
    refs are decompressed transparently on read. Bytes are cached per token.
    """

    def __init__(self, episode: ReporterEpisodeInput) -> None:
        self._episode = episode
        self.manifest: ReporterEpisodeManifest = episode.manifest
        self._cache: dict[str, bytes] = {}

    def require_success(self) -> None:
        if self.manifest.status != "success":
            raise RuntimeError(
                f"episode {self.manifest.ereq_id} status={self.manifest.status!r}; "
                "report requires successful episodes"
            )

    def _artifact_ref(self, token: str) -> ReporterArtifactRef:
        path = self.manifest.files.get(token)
        if path is None:
            raise KeyError(f"episode {self.manifest.ereq_id} has no token {token!r}")
        if not isinstance(path, str):
            raise TypeError(f"token {token!r} is multi-file, not a single file")
        ref = getattr(self._episode.artifacts, token, None)
        if not isinstance(ref, ReporterArtifactRef):
            raise KeyError(f"episode {self.manifest.ereq_id} has no artifact ref for token {token!r}")
        return ref

    def read_bytes(self, token: str) -> bytes:
        if token in self._cache:
            return self._cache[token]
        if token == "error_info":
            error_info = self._episode.inline_json.error_info
            if error_info is None:
                raise KeyError(f"episode {self.manifest.ereq_id} has no inline error_info")
            payload = json.dumps(error_info.model_dump(exclude_none=True), indent=2).encode("utf-8")
        else:
            ref = self._artifact_ref(token)
            payload = read_uri(ref.uri)
            if ref.encoding == "zlib":
                payload = zlib.decompress(payload)
        self._cache[token] = payload
        return payload

    def read_json(self, token: str) -> Any:
        return json.loads(self.read_bytes(token))

    def close(self) -> None:
        self._cache.clear()

    def __enter__(self) -> "BundleReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
