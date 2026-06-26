"""Dick mode behavior: emergency-button call plus meeting taunt."""

from __future__ import annotations

from players.crewrift.crewborg.modes.dick_mode import DICK_MODE_CHAT, DickMode
from players.crewrift.crewborg.types import ActionState, Belief


def test_dick_mode_calls_emergency_button_during_playing() -> None:
    intent = DickMode().decide(Belief(phase="Playing"), ActionState())

    assert intent.kind == "call_meeting"
    assert intent.reason == "dick mode: rush emergency button"


def test_dick_mode_taunts_once_then_votes_skip() -> None:
    mode = DickMode()
    belief = Belief(phase="Voting", phase_start_tick=100)
    action_state = ActionState()

    first = mode.decide(belief, action_state)
    assert first.kind == "chat"
    assert first.text == DICK_MODE_CHAT

    second = mode.decide(belief, action_state)
    assert second.kind == "vote"
    assert second.target_color is None

    action_state.vote_confirmed = True
    third = mode.decide(belief, action_state)
    assert third.kind == "idle"


def test_dick_mode_taunts_again_for_a_new_meeting() -> None:
    mode = DickMode()

    assert mode.decide(Belief(phase="Voting", phase_start_tick=100), ActionState()).kind == "chat"
    assert mode.decide(Belief(phase="Voting", phase_start_tick=100), ActionState()).kind == "vote"

    next_meeting = mode.decide(Belief(phase="Voting", phase_start_tick=200), ActionState())
    assert next_meeting.kind == "chat"
    assert next_meeting.text == DICK_MODE_CHAT
