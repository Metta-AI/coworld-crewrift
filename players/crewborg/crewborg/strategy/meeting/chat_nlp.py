"""spaCy lifecycle for meeting chat parsing (design §10.5).

Loading ``en_core_web_sm`` costs ~0.5 s on a full core — and ~1.5–2 s under the
hosted ¼-core cap, ~40 frames. So we **never** load it on the gameplay hot path:
``ensure_loading()`` kicks off a background daemon thread (idempotent) that loads the
model, and ``get_model()`` returns it only once ready (``None`` until then). Callers
fall back gracefully — no chat signal — while it loads. Started at agent build so the
load overlaps the pre-game idle phases and is ready before the first meeting.

The whole thing is gated by ``CREWBORG_CHAT_NLP`` (default on) — a runtime kill switch:
unset it (``=0``) and we never import spaCy or load the model, and chat parsing is off
(the imposter bandwagon falls back to the reliable vote signal only). spaCy is imported
**only** inside the loader thread, so a disabled agent never pays the import.
"""

from __future__ import annotations

import os
import threading
from typing import Any

_ENV_FLAG = "CREWBORG_CHAT_NLP"
_DISABLED_VALUES = {"0", "false", "no", "off"}

_lock = threading.Lock()
_model: Any | None = None
_thread: threading.Thread | None = None
_failed = False


def is_enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "1").strip().lower() not in _DISABLED_VALUES


def ensure_loading() -> None:
    """Start the background model load once, if enabled. Idempotent and non-blocking."""

    if not is_enabled():
        return
    global _thread
    with _lock:
        if _thread is not None or _model is not None or _failed:
            return
        _thread = threading.Thread(target=_load, name="crewborg-spacy-load", daemon=True)
        _thread.start()


def get_model() -> Any | None:
    """The loaded spaCy pipeline, or ``None`` if disabled / still loading / failed."""

    return _model


def state() -> str:
    """A one-word status for tracing: ``disabled`` / ``loading`` / ``ready`` / ``failed``."""

    if not is_enabled():
        return "disabled"
    if _model is not None:
        return "ready"
    return "failed" if _failed else "loading"


def _load() -> None:
    global _model, _failed
    try:
        import spacy  # imported only here, so a disabled agent never pays for it

        # We only need the dependency parse (negation scope) + tagger it depends on;
        # NER is dead weight for this task and slows the load.
        nlp = spacy.load("en_core_web_sm", disable=["ner"])
        with _lock:
            _model = nlp
    except Exception:  # missing model / import error — degrade to no chat signal
        with _lock:
            _failed = True
