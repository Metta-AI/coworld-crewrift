"""Meeting-context serialization: turn ``Belief`` into the compact JSON the meeting LLM sees.

``serialize_meeting_context`` is the single projection of game state the model reasons over
for one meeting tick. It is deliberately explicit and pre-digested — it spells out the legal
vote targets, the deterministic fallback vote and *why*, the live vote tally, the chat log
(flagging which lines are our own), per-player records with recent events, and the full
suspicion ranking. The aim is that the model decides over already-computed signals rather
than re-deriving them, and that everything it might output (a vote target, a chat line) has a
legal example present in the context.

``valid_vote_targets`` is shared with ``modes.attend_meeting`` and ``schema.validate_meeting_decision``
as the single source of truth for "who may be voted for", so the legality the prompt
advertises is exactly the legality validation enforces.

Collaborators
-------------
Relies on:
  - ``strategy.suspicion`` — ``top_suspect`` (the deterministic fallback vote),
    ``witnessed_imposters``, ``_prior_imposter_p``, ``VOTE_PROBABILITY`` (the vote bar).
  - ``schema`` — ``SCHEMA_VERSION`` / ``VOTE_SKIP`` / ``CHAT_MAX_CHARS`` echoed into the
    context's constraints.
  - ``perception.entities.SKIP_VOTE_TARGET`` — the raw skip marker, mapped to ``VOTE_SKIP``.
  - ``types`` — ``Belief`` / ``PlayerEvent`` / ``PlayerRecord`` read fields.
Used by:
  - ``modes.attend_meeting`` builds the context each LLM tick and reuses ``valid_vote_targets``.
  - ``llm.AnthropicMeetingClient.decide`` ships the returned dict as the user message.

Modifying this file: ``valid_vote_targets`` must stay the one definition of legal targets
(prompt and validation both call it) — diverging here would let the model be told something
is legal that validation then rejects, forcing avoidable fallbacks. ``serialize_meeting_context``
has no side effects; keep it a pure read of belief.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from crewborg.perception.entities import SKIP_VOTE_TARGET
from crewborg.strategy.meeting.schema import CHAT_MAX_CHARS, SCHEMA_VERSION, VOTE_SKIP
from crewborg.strategy.suspicion import (
    VOTE_PROBABILITY,
    _prior_imposter_p,
    top_suspect,
    witnessed_imposters,
)
from crewborg.types import Belief, PlayerEvent, PlayerRecord

#: Length of the meeting vote window, in ticks. Used to report meeting age / estimated
#: remaining time to the model (and, via ``attend_meeting``, to time the auto-submit).
VOTE_TIMER_TICKS = 240
#: Minimum ticks between our own chat messages. The context reports whether the cooldown is
#: ready so the model doesn't propose chat that would be suppressed.
CHAT_COOLDOWN_TICKS = 100


def serialize_meeting_context(
    belief: Belief,
    *,
    trigger: str,
    tentative_vote: str | None = None,
    sent_chat_texts: set[str] | None = None,
    last_chat_tick: int | None = None,
) -> dict[str, Any]:
    """Serialize belief into the compact, explicit context the meeting LLM sees.

    ``trigger`` names why this tick called the model (echoed for the model's awareness).
    ``tentative_vote`` is the vote staged so far (``None`` if none). ``sent_chat_texts`` are
    lines we've already said, so chat from us is flagged ``self`` even when the engine didn't
    attribute a speaker color. ``last_chat_tick`` drives the ``chat_cooldown_ready`` flag.
    Returns a plain JSON-serializable dict; pure read of ``belief`` (no side effects)."""

    sent_chat_texts = sent_chat_texts or set()
    age_ticks = max(0, belief.last_tick - belief.phase_start_tick)
    remaining_ticks = max(0, VOTE_TIMER_TICKS - age_ticks)
    legal_targets = sorted(valid_vote_targets(belief))
    fallback_vote = _fallback_vote_target(belief)
    return {
        "schema_version": SCHEMA_VERSION,
        "trigger": trigger,
        "meeting": {
            "id": belief.phase_start_tick,
            "phase": belief.phase,
            "tick": belief.last_tick,
            "age_ticks": age_ticks,
            "estimated_remaining_ticks": remaining_ticks,
            "vote_timer_ticks": VOTE_TIMER_TICKS,
        },
        "self": {
            "color": belief.voting.self_marker_color,
            "role": belief.self_role,
            "teammates": sorted(belief.teammate_colors),
        },
        "constraints": {
            "actions": ["send_chat", "set_tentative_vote", "submit_vote", "wait"],
            "valid_vote_targets": [*legal_targets, VOTE_SKIP],
            "skip_vote_target": VOTE_SKIP,
            "chat_max_chars": CHAT_MAX_CHARS,
            "chat_must_be_printable_ascii": True,
            "chat_cooldown_ticks": CHAT_COOLDOWN_TICKS,
            "chat_cooldown_ready": _chat_ready(belief, last_chat_tick),
        },
        "state": {
            "tentative_vote": tentative_vote,
            "fallback_vote": fallback_vote,
            "fallback_vote_reason": _fallback_vote_reason(belief, fallback_vote),
        },
        "voting": _voting_payload(belief),
        "chat": _chat_payload(belief, sent_chat_texts),
        "players": _players_payload(belief),
        "suspicion": _suspicion_payload(belief, fallback_vote),
    }


def valid_vote_targets(belief: Belief) -> set[str]:
    """Return live player colors the LLM may target, excluding self."""

    self_color = belief.voting.self_marker_color
    candidates = {
        candidate.color
        for candidate in belief.voting.candidates
        if candidate.alive and candidate.color != self_color
    }
    if candidates:
        return candidates
    return {
        color
        for color, record in belief.roster.items()
        if record.life_status == "alive" and color != self_color
    }


def _fallback_vote_target(belief: Belief) -> str:
    """The deterministic vote the agent would cast with no LLM: the clear ``top_suspect`` or
    ``VOTE_SKIP`` on a flat field. Shown to the model as ``state.fallback_vote``."""

    return top_suspect(belief) or VOTE_SKIP


def _fallback_vote_reason(belief: Belief, fallback_vote: str) -> str:
    """Human-readable justification for the fallback vote (skip vs named suspect + its
    P(imposter)), surfaced so the model can weigh the deterministic restraint signal."""

    if fallback_vote == VOTE_SKIP:
        return f"no suspect at or above vote bar {VOTE_PROBABILITY}"
    p = belief.suspicion.get(fallback_vote)
    return f"top suspect {fallback_vote} at P(imposter)={p:.4f}" if p is not None else "top suspect"


def _chat_ready(belief: Belief, last_chat_tick: int | None) -> bool:
    """Whether the chat cooldown has elapsed since our last message (always true if we
    haven't spoken). Mirrors ``attend_meeting``'s own cooldown check."""

    return last_chat_tick is None or belief.last_tick - last_chat_tick >= CHAT_COOLDOWN_TICKS


def _voting_payload(belief: Belief) -> dict[str, Any]:
    """The live ballot state: cursor position, candidate rows (with self/teammate/suspicion
    flags), each cast vote mapped slot→color, and a target→count tally. Raw skip-marker
    targets are mapped to ``VOTE_SKIP`` so the model sees one consistent skip token."""

    voting = belief.voting
    slot_to_color = {candidate.slot: candidate.color for candidate in voting.candidates}
    dots = []
    tally: Counter[str] = Counter()
    for dot in voting.dots:
        target = VOTE_SKIP if dot.target == SKIP_VOTE_TARGET else slot_to_color.get(dot.target, str(dot.target))
        tally[target] += 1
        dots.append(
            {
                "voter_slot": dot.voter,
                "voter_color": slot_to_color.get(dot.voter),
                "target": target,
            }
        )
    return {
        "cursor_slot": voting.cursor_slot,
        "cursor_on_skip": voting.skip_cursor_present,
        "timer_present": voting.timer_present,
        "candidates": [
            {
                "slot": candidate.slot,
                "color": candidate.color,
                "alive": candidate.alive,
                "self": candidate.color == voting.self_marker_color,
                "teammate": candidate.color in belief.teammate_colors,
                "suspicion": _rounded(belief.suspicion.get(candidate.color)),
            }
            for candidate in voting.candidates
        ],
        "votes": dots,
        "tally": dict(sorted(tally.items())),
    }


def _chat_payload(belief: Belief, sent_chat_texts: set[str]) -> dict[str, Any]:
    """The meeting chat log as messages, each flagged ``self`` if we spoke it (by speaker
    color or because the text is in ``sent_chat_texts`` — the engine doesn't always attach a
    speaker color to our own lines)."""

    self_color = belief.voting.self_marker_color
    return {
        "messages": [
            {
                "tick": event.tick,
                "speaker_color": event.speaker_color,
                "self": event.speaker_color == self_color or event.text in sent_chat_texts,
                "text": event.text,
            }
            for event in belief.chat_log
        ]
    }


def _players_payload(belief: Belief) -> list[dict[str, Any]]:
    """All roster rows, color-sorted for a stable prompt ordering."""

    return [
        _player_payload(belief, color, record)
        for color, record in sorted(belief.roster.items())
    ]


def _player_payload(belief: Belief, color: str, record: PlayerRecord) -> dict[str, Any]:
    """One roster row for the model: identity/role flags, life status, last-seen position +
    age, death/body info, suspicion, the witnessed/believed-imposter flags, and the last few
    behavior events. ``recent_events`` is capped to the most recent 8 to bound prompt size."""

    age = None if record.last_seen_tick == 0 else max(0, belief.last_tick - record.last_seen_tick)
    return {
        "color": color,
        "self": color == belief.voting.self_marker_color,
        "teammate": color in belief.teammate_colors,
        "life_status": record.life_status,
        "last_seen_tick": record.last_seen_tick or None,
        "last_seen_age_ticks": age,
        "last_seen_xy": [record.world_x, record.world_y] if record.last_seen_tick else None,
        "death_seen_tick": record.death_seen_tick,
        "death_source": record.death_source,
        "body_xy": list(record.body_xy) if record.body_xy is not None else None,
        "suspicion": _rounded(belief.suspicion.get(color)),
        "confirmed_imposter": color in witnessed_imposters(belief),
        "believed_imposter": color in belief.believed_imposters,
        "recent_events": [_event_payload(belief, event) for event in record.events[-8:]],
    }


def _event_payload(belief: Belief, event: PlayerEvent) -> dict[str, Any]:
    """One behavior event (kind, timing, target, region, min distance), with a resolved
    ``region_name`` added when the map is loaded."""

    payload: dict[str, Any] = {
        "kind": event.kind,
        "start_tick": event.start_tick,
        "end_tick": event.end_tick,
        "duration_ticks": event.duration_ticks,
        "target_color": event.target_color,
        "region_index": event.region_index,
        "min_dist": event.min_dist,
    }
    if belief.map is not None and event.region_index is not None:
        payload["region_name"] = _region_name(belief, event)
    return payload


def _region_name(belief: Belief, event: PlayerEvent) -> str | None:
    """Resolve an event's ``region_index`` to a human map label (room/task name, or
    ``vent <group>:<index>``) when the map is loaded and the index is in range; else ``None``."""

    assert belief.map is not None
    if event.kind == "room" and 0 <= event.region_index < len(belief.map.rooms):
        return belief.map.rooms[event.region_index].name
    if event.kind == "task" and 0 <= event.region_index < len(belief.map.tasks):
        return belief.map.tasks[event.region_index].name
    if event.kind == "vent" and 0 <= event.region_index < len(belief.map.vents):
        vent = belief.map.vents[event.region_index]
        return f"vent {vent.group}:{vent.group_index}"
    return None


def _suspicion_payload(belief: Belief, fallback_vote: str) -> dict[str, Any]:
    """The suspicion model's summary: prior, the vote bar, confirmed/believed imposter sets,
    the full per-color P(imposter) ranking (descending), and the deterministic ``would_vote``."""

    return {
        "prior": _rounded(_prior_imposter_p(belief)),
        "vote_probability_threshold": VOTE_PROBABILITY,
        "confirmed": sorted(witnessed_imposters(belief)),
        "believed": sorted(belief.believed_imposters),
        "ranking": [
            {"color": color, "p": _rounded(p)}
            for color, p in sorted(belief.suspicion.items(), key=lambda item: item[1], reverse=True)
        ],
        "would_vote": fallback_vote,
    }


def _rounded(value: float | None) -> float | None:
    """Round a probability to 4 dp for a compact prompt (``None`` passes through)."""

    return None if value is None else round(value, 4)
