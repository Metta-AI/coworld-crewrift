"""Meeting-context serialization for LLM chat/vote decisions."""

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

VOTE_TIMER_TICKS = 240
# Min ticks between our own chats. Kept well under VOTE_TIMER_TICKS so a proactive
# meeting voice can speak more than once (share a read, then react/follow up).
CHAT_COOLDOWN_TICKS = 60


def serialize_meeting_context(
    belief: Belief,
    *,
    trigger: str,
    tentative_vote: str | None = None,
    sent_chat_texts: set[str] | None = None,
    last_chat_tick: int | None = None,
) -> dict[str, Any]:
    """Serialize belief into the compact, explicit context the meeting LLM sees."""

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
    return top_suspect(belief) or VOTE_SKIP


def _fallback_vote_reason(belief: Belief, fallback_vote: str) -> str:
    if fallback_vote == VOTE_SKIP:
        return f"no suspect at or above vote bar {VOTE_PROBABILITY}"
    p = belief.suspicion.get(fallback_vote)
    return f"top suspect {fallback_vote} at P(imposter)={p:.4f}" if p is not None else "top suspect"


def _chat_ready(belief: Belief, last_chat_tick: int | None) -> bool:
    return last_chat_tick is None or belief.last_tick - last_chat_tick >= CHAT_COOLDOWN_TICKS


def _voting_payload(belief: Belief) -> dict[str, Any]:
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
    return [
        _player_payload(belief, color, record)
        for color, record in sorted(belief.roster.items())
    ]


def _player_payload(belief: Belief, color: str, record: PlayerRecord) -> dict[str, Any]:
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
    return None if value is None else round(value, 4)
