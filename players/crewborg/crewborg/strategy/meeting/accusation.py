"""Build a crewmate's meeting accusation from the suspect's event log.

When the deterministic meeting path votes a clear leading suspect (``top_suspect``),
it announces *why* — ``"<color> sus: <reason 1>, <reason 2>"`` — instead of a generic
opener. Each reason is a short template for one kind of evidence the suspicion model
weighs, and they are ordered by how much that evidence moved the posterior, so the
chat leads with the most important point.

This is the presentation layer over the suspicion model: it reuses the per-event
log-LR functions (``suspicion.py``) to *rank* a suspect's evidence, then maps each
winning cue to a human phrase. Keep the phrasing here; keep the scoring in
``suspicion.py``.

Both a crewmate's real accusation (``build_accusation``) and an imposter's *fabricated*
one (``fabricate_accusation``) render through the **same** ``_format`` template, so the
chat shape is identical and the accusation is not a role tell (design §10.4) — the only
difference is real vs invented evidence, not the wording or structure.

Collaborators
-------------
Relies on:
  - ``strategy.suspicion`` — the per-event log-LR scorers (``_tailing_self_log_lr``,
    ``_follow_log_lr``, ``_body_proximity_log_lr``, ``_vent_dwell_log_lr``) and
    ``WITNESSED_LOG_LR``, reused to *rank* evidence (not to recompute suspicion).
  - ``schema.CHAT_MAX_CHARS`` — the hard cap the rendered line is truncated to.
  - ``types`` — ``Belief`` (roster/events) and ``PlayerEvent`` (real and synthetic).
Used by: ``modes.attend_meeting`` — ``build_accusation`` for the crewmate/imposter real
  deflection; ``fabricate_accusation`` for the imposter bandwagon.

Modifying this file: keep ``build_accusation`` and ``fabricate_accusation`` rendering
through the same ``_format`` (the anti-tell). Fabricated accusations must cite only
**safe, hard-to-disprove** cues — never a bold witnessed kill/vent another player could
contradict. This module reads belief and returns a string; it has no game side effects.
"""

from __future__ import annotations

from crewborg.strategy.meeting.schema import CHAT_MAX_CHARS
from crewborg.strategy.suspicion import (
    WITNESSED_LOG_LR,
    _body_proximity_log_lr,
    _follow_log_lr,
    _tailing_self_log_lr,
    _vent_dwell_log_lr,
)
from crewborg.types import Belief, PlayerEvent

# Cite at most this many reasons — the strongest cues — so the line stays readable.
MAX_REASONS = 3


def build_accusation(belief: Belief, color: str) -> str | None:
    """``"<color> sus: reason, reason"`` ranked strongest-first, or ``None`` when the
    suspect's log carries no citable evidence (so the caller stays silent)."""

    reasons = _ranked_reasons(belief, color)
    if not reasons:
        return None
    return _format(color, reasons)


def fabricate_accusation(belief: Belief, color: str) -> str | None:
    """A **fabricated** accusation against ``color``, in the *identical* format a real
    one uses (the anti-tell — see design §10.4). For the imposter's bandwagon: it has
    no real evidence, so it cites **safe, hard-to-disprove** cues only — never a bold,
    falsifiable witnessed kill/vent that another player could contradict.

    A real body (if any) anchors the most persuasive safe claim ("next to X's body");
    otherwise it falls back to a tail/vent claim. Returns ``None`` only if even the
    fallbacks can't be formed (never, in practice)."""

    reasons: list[str] = []
    victim = _a_dead_color(belief)
    if victim is not None:
        reasons.append(_phrase_near_body(_synthetic("near_body", target_color=victim)))
    else:
        reasons.append(_phrase_tail(_synthetic("tailing_self")))
    reasons.append(_phrase_vent_dwell(_synthetic("vent")))
    return _format(color, reasons)


def _format(color: str, reasons: list[str]) -> str:
    """The shared render — ``"<color> sus: r1, r2"`` (top ``MAX_REASONS``), truncated to
    ``CHAT_MAX_CHARS``. The single template both real and fabricated accusations use, so the
    line is the same shape either way (the anti-tell)."""

    line = f"{color} sus: {', '.join(reasons[:MAX_REASONS])}"
    return line[:CHAT_MAX_CHARS]


def _synthetic(kind: str, *, target_color: str | None = None) -> PlayerEvent:
    """A zero-time placeholder event so a fabricated accusation can reuse the same phrase
    helpers as a real one (the phrasers read ``target_color`` but ignore timing)."""

    return PlayerEvent(kind=kind, start_tick=0, end_tick=0, target_color=target_color)


def _a_dead_color(belief: Belief) -> str | None:
    """The most-recently-dead non-teammate color (a real body to name), or ``None``."""

    dead = [
        r for r in belief.roster.values()
        if r.life_status == "dead" and r.color not in belief.teammate_colors
    ]
    if not dead:
        return None
    return max(dead, key=lambda r: r.death_seen_tick or 0).color


def _ranked_reasons(belief: Belief, color: str) -> list[str]:
    """The suspect's evidence as phrases, ordered by each cue's log-LR (descending).

    One phrase per evidence *type* (its most-suspicious instance), mirroring how the
    posterior aggregates with ``max`` per type — so the chat doesn't repeat a cue.
    """

    record = belief.roster.get(color)
    if record is None:
        return []

    scored: list[tuple[float, str]] = []
    _add_witnessed(scored, record.events)
    _add_strongest(scored, record.events, "tailing_self", lambda e: _tailing_self_log_lr(e), _phrase_tail)
    _add_strongest(scored, record.events, "proximity", lambda e: _follow_log_lr(e, belief), _phrase_follow)
    _add_strongest(scored, record.events, "near_body", _body_proximity_log_lr, _phrase_near_body)
    _add_strongest(scored, record.events, "vent", _vent_dwell_log_lr, _phrase_vent_dwell)

    scored.sort(key=lambda item: item[0], reverse=True)
    return [phrase for _, phrase in scored]


def _add_witnessed(scored: list[tuple[float, str]], events: list[PlayerEvent]) -> None:
    """A witnessed catch is the strongest cue; cite the kill (with victim) or vent."""

    if any(e.kind == "kill" for e in events):
        victim = next(e.target_color for e in events if e.kind == "kill")
        scored.append((WITNESSED_LOG_LR + 1.0, f"saw them kill {victim}"))
    if any(e.kind == "vent_use" for e in events):
        scored.append((WITNESSED_LOG_LR, "saw them vent"))


def _add_strongest(scored, events, kind, log_lr, phrase) -> None:
    """Add the single most-suspicious instance of ``kind`` (if any cleared 0 log-LR)."""

    best: tuple[float, PlayerEvent] | None = None
    for event in events:
        if event.kind != kind:
            continue
        lr = log_lr(event)
        if lr > 0.0 and (best is None or lr > best[0]):
            best = (lr, event)
    if best is not None:
        scored.append((best[0], phrase(best[1])))


# Cue → human phrase. ``_phrase_follow`` / ``_phrase_near_body`` name the victim color from
# the event; ``_phrase_tail`` / ``_phrase_vent_dwell`` take no detail (the ``event`` arg is
# accepted only so all phrasers share one signature for ``_add_strongest``).
def _phrase_tail(event: PlayerEvent) -> str:
    return "they were tailing me"


def _phrase_follow(event: PlayerEvent) -> str:
    return f"followed {event.target_color} before they died"


def _phrase_near_body(event: PlayerEvent) -> str:
    return f"next to {event.target_color}'s body"


def _phrase_vent_dwell(event: PlayerEvent) -> str:
    return "lurking on a vent"
