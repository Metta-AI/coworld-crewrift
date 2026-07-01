"""Accusation-chat builder tests (the '<color> sus: reasons' meeting line)."""

from __future__ import annotations

from crewborg.strategy.meeting.accusation import MAX_REASONS, build_accusation
from crewborg.types import Belief, PlayerEvent, PlayerRecord


def _belief_with(color: str, events: list[PlayerEvent], **roster_kwargs) -> Belief:
    belief = Belief(self_role="crewmate", total_player_count=8)
    belief.roster[color] = PlayerRecord(color=color, life_status="alive", events=events, **roster_kwargs)
    return belief


def test_no_events_yields_no_accusation() -> None:
    belief = _belief_with("red", [])
    assert build_accusation(belief, "red") is None


def test_unknown_color_yields_no_accusation() -> None:
    belief = Belief(self_role="crewmate")
    assert build_accusation(belief, "red") is None


def test_witnessed_kill_names_the_victim_and_leads() -> None:
    belief = _belief_with("red", [PlayerEvent(kind="kill", start_tick=5, end_tick=5, target_color="green")])
    assert build_accusation(belief, "red") == "red sus: saw them kill green"


def test_witnessed_vent_phrasing() -> None:
    belief = _belief_with("red", [PlayerEvent(kind="vent_use", start_tick=5, end_tick=5)])
    assert build_accusation(belief, "red") == "red sus: saw them vent"


def test_being_tailed_phrasing() -> None:
    belief = _belief_with("red", [PlayerEvent(kind="tailing_self", start_tick=1, end_tick=50, target_color=None)])
    assert build_accusation(belief, "red") == "red sus: they were tailing me"


def test_a_kill_outranks_a_tail_and_leads_the_line() -> None:
    belief = _belief_with(
        "red",
        [
            PlayerEvent(kind="tailing_self", start_tick=1, end_tick=50, target_color=None),
            PlayerEvent(kind="kill", start_tick=60, end_tick=60, target_color="green"),
        ],
    )
    line = build_accusation(belief, "red")
    assert line == "red sus: saw them kill green, they were tailing me"  # strongest first


def test_each_evidence_type_is_cited_once() -> None:
    # Two vent dwells ⇒ a single "lurking on a vent" reason (max per type, no repeats).
    belief = _belief_with(
        "red",
        [
            PlayerEvent(kind="vent", start_tick=1, end_tick=10, region_index=0),
            PlayerEvent(kind="vent", start_tick=20, end_tick=40, region_index=0),
        ],
    )
    assert build_accusation(belief, "red") == "red sus: lurking on a vent"


def test_at_most_max_reasons_are_cited() -> None:
    # Five distinct cues present; only the strongest MAX_REASONS appear.
    belief = _belief_with(
        "red",
        [
            PlayerEvent(kind="kill", start_tick=70, end_tick=70, target_color="green"),
            PlayerEvent(kind="vent_use", start_tick=65, end_tick=65),
            PlayerEvent(kind="tailing_self", start_tick=1, end_tick=50, target_color=None),
            PlayerEvent(kind="near_body", start_tick=55, end_tick=56, target_color="green", min_dist=8),
            PlayerEvent(kind="vent", start_tick=1, end_tick=10, region_index=0),
        ],
        death_seen_tick=None,
    )
    line = build_accusation(belief, "red")
    assert line is not None
    assert line.count(",") == MAX_REASONS - 1  # exactly MAX_REASONS reasons
    assert line.startswith("red sus: saw them kill green")  # witnessed kill leads
