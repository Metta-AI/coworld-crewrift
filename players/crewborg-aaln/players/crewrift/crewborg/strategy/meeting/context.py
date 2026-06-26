"""Meeting-context serialization for LLM chat/vote decisions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from players.crewrift.crewborg.perception.entities import SKIP_VOTE_TARGET
from players.crewrift.crewborg.strategy.meeting.schema import CHAT_MAX_CHARS, SCHEMA_VERSION, VOTE_SKIP
from players.crewrift.crewborg.strategy.meeting.vote_policy import (
    alive_count,
    anti_split_swap,
    imposters_remaining,
    must_eject,
    plurality_target,
    vote_bar,
)
from players.crewrift.crewborg.strategy.meeting.vote_policy import (
    fallback_vote as _policy_fallback_vote,
)
from players.crewrift.crewborg.strategy.suspicion import _imposter_count, _prior_imposter_p
from players.crewrift.crewborg.types import Belief, PlayerEvent, PlayerRecord

# Conservative fallback meeting length. The live value is learned from the
# pre-game GAME INFO interstitial (``belief.vote_timer_ticks``; the current game
# default is 1200 = 50 s, upstream 2026-06-10). The fallback deliberately stays at
# the OLD 240-tick default: when the timer is unknown, assuming a short meeting
# only submits the vote early, while assuming a long one on a short-timer server
# would miss the deadline and eat the −10 no-vote penalty.
VOTE_TIMER_TICKS = 240
CHAT_COOLDOWN_TICKS = 100


def effective_vote_timer_ticks(belief: Belief) -> int:
    """This episode's meeting length: the game-info value, else the safe fallback."""

    return belief.vote_timer_ticks or VOTE_TIMER_TICKS


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
    vote_timer_ticks = effective_vote_timer_ticks(belief)
    age_ticks = max(0, belief.last_tick - belief.phase_start_tick)
    remaining_ticks = max(0, vote_timer_ticks - age_ticks)
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
            "vote_timer_ticks": vote_timer_ticks,
            # Who opened this meeting and how (from the meeting-call interstitial;
            # null on older servers): "report" meetings name the reported body.
            "called_by": belief.meeting_called_by,
            "call_trigger": belief.meeting_trigger,
            "reported_body_color": belief.meeting_reported_body_color,
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
        "social": _social_payload(belief),
        "game_state": _game_state_payload(belief, fallback_vote, remaining_ticks),
    }


def valid_vote_targets(belief: Belief) -> set[str]:
    """Return live player colors we are allowed to vote for, excluding self.

    When we are the imposter, teammates are also excluded: this is the single
    chokepoint that feeds the LLM's legal-target menu, the decision validator,
    and the submit-time legality re-check, so excluding teammates here hard-stops
    crewborg from ever voting one out — the prompt instructs the model not to, and
    this enforces it regardless of what the model returns. (This deliberately
    forecloses "bussing" a teammate for cover; an accidental teammate vote is the
    far likelier and costlier failure for a fast meeting model.) ``skip`` is
    always legal and is handled separately, so an imposter left with only teammates
    alive still safely skips rather than deadlocking.
    """

    excluded = _excluded_vote_colors(belief)
    candidates = {
        candidate.color
        for candidate in belief.voting.candidates
        if candidate.alive and candidate.color not in excluded
    }
    if candidates:
        return candidates
    return {
        color
        for color, record in belief.roster.items()
        if record.life_status == "alive" and color not in excluded
    }


def _excluded_vote_colors(belief: Belief) -> set[str]:
    excluded: set[str] = set()
    if belief.voting.self_marker_color is not None:
        excluded.add(belief.voting.self_marker_color)
    if belief.self_role == "imposter":
        excluded |= belief.teammate_colors
    return excluded


def _fallback_vote_target(belief: Belief) -> str:
    return _policy_fallback_vote(belief)


def _fallback_vote_reason(belief: Belief, fallback_vote: str) -> str:
    if belief.self_role == "imposter":
        return "join the crew plurality" if fallback_vote != VOTE_SKIP else "no crew plurality to join"
    if fallback_vote == VOTE_SKIP:
        return f"no suspect at or above vote bar {vote_bar(belief)}"
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
        "confirmed_imposter": color in belief.confirmed_imposters,
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
        "vote_probability_threshold": vote_bar(belief),
        "confirmed": sorted(belief.confirmed_imposters),
        "believed": sorted(belief.believed_imposters),
        "ranking": [
            {"color": color, "p": _rounded(p)}
            for color, p in sorted(belief.suspicion.items(), key=lambda item: item[1], reverse=True)
        ],
        "would_vote": fallback_vote,
    }


def _social_payload(belief: Belief) -> dict[str, Any]:
    """The who-sus'd-who record: accusation graph + prior meetings' votes."""

    return {
        "accusations": [
            {
                "meeting_id": accusation.meeting_id,
                "tick": accusation.tick,
                "speaker_color": accusation.speaker_color,
                "target_color": accusation.target_color,
                "stance": accusation.stance,
                "text": accusation.text,
            }
            for accusation in belief.accusations
        ],
        "meetings": [
            {
                "meeting_id": record.meeting_id,
                "votes": dict(sorted(record.votes.items())),
                "ejected_color": record.ejected_color,
                "ejected_was_confirmed_imposter": (
                    record.ejected_color in belief.confirmed_imposters
                    if record.ejected_color is not None
                    else None
                ),
            }
            for record in belief.meeting_history
        ],
    }


def _game_state_payload(belief: Belief, fallback_vote: str, remaining_ticks: int) -> dict[str, Any]:
    """Game-theory state for the vote: parity margin, endgame flags, anti-split."""

    imps = imposters_remaining(belief)
    alive = alive_count(belief)
    plurality = plurality_target(belief)
    anti_split = anti_split_swap(belief, fallback_vote, remaining_ticks)
    return {
        "alive_players": alive,
        "imposters_total": _imposter_count(belief),
        "imposters_remaining": imps,
        "alive_crew": alive - imps,
        "must_eject": must_eject(belief),
        "vote_bar": vote_bar(belief),
        "plurality_target": plurality,
        "anti_split_recommendation": anti_split if anti_split != fallback_vote else None,
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
