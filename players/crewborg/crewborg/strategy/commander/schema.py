"""Validate raw commander LLM JSON into a safe, typed ``CommanderPriorities``.

The trust boundary between untrusted LLM output and the modes that consume it. Raw JSON
(from the LLM, or from the ``CREWBORG_COMMANDER_FORCE`` test override) is never installed
into belief directly — it passes through ``sanitize_priorities`` first, which drops any
field that names an illegal room/player, coerces enums to their default, and gates the
two DANGER levers behind a non-empty ``danger_reason``. Whatever survives is, by
construction, safe for a mode to act on, so "commander off / garbage in = inert".

Collaborators
-------------
Relies on:
  - ``types.CommanderPriorities`` — the frozen, ``extra="forbid"`` target model.
  - the ``legal_rooms`` / ``legal_players`` sets (from ``context.py``) as the validity sets.
Used by:
  - ``strategy.CommanderStrategy.decide`` — sanitizes both worker output and forced
    priorities before stamping ``as_of_tick`` and publishing as the ``commander`` inference.

Modifying this file: this is a trust boundary — every field must default to the
behavior-preserving value when input is missing or malformed. The two DANGER levers
(``allow_witnessed_kill`` / ``skip_evade``) MUST stay gated on ``has_danger_reason``;
that is what stops the LLM from silently turning on risky play.

``strength`` is SOFT-ONLY on the live LLM path, **by design**: the model is never asked for
it (``llm.py``'s ``response_schema`` and the prompts omit ``strength``), so LLM-authored
priorities always sanitize to ``"soft"`` — bias, never force. ``"hard"`` forcing (read by
``modes.normal`` / ``modes.search`` as "force, don't just bias") is reachable ONLY through
the ``CREWBORG_COMMANDER_FORCE`` override — a deterministic test / QA / control path the LLM
cannot trigger. Keep it this way unless you intend to let the LLM hard-force modes.
"""

from __future__ import annotations

from typing import Any

from crewborg.types import CommanderPriorities

#: Accepted ``posture`` values; anything else is coerced to the behavior-neutral default.
_VALID_POSTURES = {"stick", "isolate", "neutral"}
#: Accepted ``strength`` values; ``"soft"`` = bias only, ``"hard"`` = force. Soft-only on the
#: live LLM path; ``"hard"`` only via CREWBORG_COMMANDER_FORCE (see module docstring).
_VALID_STRENGTHS = {"soft", "hard"}


def sanitize_priorities(
    raw: dict[str, Any],
    legal_rooms: set[str],
    legal_players: set[str],
    *,
    as_of_tick: int,
) -> CommanderPriorities:
    """Return a safe commander payload, dropping invalid or stale-by-construction fields.

    Each field is reduced to a value a mode can trust: room/player fields must be present
    in ``legal_rooms`` / ``legal_players`` (else ``None``); ``target_task`` must be a real
    ``int`` (``bool`` is rejected — ``type(...) is int`` excludes ``True``/``False``);
    ``posture`` / ``strength`` fall back to their neutral defaults on any unknown value; and
    the two DANGER levers are forced ``False`` unless a non-empty ``danger_reason`` string is
    supplied. ``as_of_tick`` stamps the freshness clock that ``bias.commander_of`` reads for
    its TTL. The returned model is frozen."""

    danger_reason = raw.get("danger_reason")
    has_danger_reason = isinstance(danger_reason, str) and bool(danger_reason.strip())
    allow_witnessed_kill = bool(raw.get("allow_witnessed_kill")) and has_danger_reason
    skip_evade = bool(raw.get("skip_evade")) and has_danger_reason

    target_task = raw.get("target_task")
    if type(target_task) is not int:
        target_task = None

    posture = raw.get("posture")
    if posture not in _VALID_POSTURES:
        posture = "neutral"
    strength = raw.get("strength")
    if strength not in _VALID_STRENGTHS:
        strength = "soft"

    return CommanderPriorities(
        target_room=_legal_string(raw.get("target_room"), legal_rooms),
        target_task=target_task,
        posture=posture,
        strength=strength,
        hunt_room=_legal_string(raw.get("hunt_room"), legal_rooms),
        target_player=_legal_string(raw.get("target_player"), legal_players),
        avoid_room=_legal_string(raw.get("avoid_room"), legal_rooms),
        allow_witnessed_kill=allow_witnessed_kill,
        skip_evade=skip_evade,
        danger_reason=danger_reason.strip() if (allow_witnessed_kill or skip_evade) else None,
        reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        as_of_tick=as_of_tick,
    )


def _legal_string(value: Any, legal_values: set[str]) -> str | None:
    """Return ``value`` only when it is a string present in ``legal_values``; otherwise ``None``."""
    return value if isinstance(value, str) and value in legal_values else None
