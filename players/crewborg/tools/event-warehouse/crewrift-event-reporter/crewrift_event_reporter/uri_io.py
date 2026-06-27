from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5


def _file_path_from_uri(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


def read_uri(uri: str) -> bytes:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        return _file_path_from_uri(uri).read_bytes()
    if scheme == "":
        return Path(uri).read_bytes()
    if scheme in {"http", "https"}:
        return _http_request_with_retry("GET", uri).content
    raise ValueError(f"unsupported URI scheme for read: {scheme!r}")


def write_uri(uri: str, payload: bytes, content_type: str, method: str = "PUT") -> None:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        path = _file_path_from_uri(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return
    if scheme == "":
        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return
    if scheme in {"http", "https"}:
        _http_request_with_retry(method, uri, content=payload, headers={"Content-Type": content_type})
        return
    raise ValueError(f"unsupported URI scheme for write: {scheme!r}")


def _http_request_with_retry(
    method: str,
    uri: str,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    delay = 0.5
    with httpx.Client(timeout=60) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = client.request(method, uri, content=content, headers=headers)
            if response.status_code < 400:
                return response
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                response.raise_for_status()
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
    raise RuntimeError("unreachable HTTP retry loop")
