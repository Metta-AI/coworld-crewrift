from __future__ import annotations

import pytest

from crewborg.strategy.commander.schema import sanitize_priorities
from crewborg.types import Belief, CommanderPriorities


def test_belief_defaults_commander_none() -> None:
    assert Belief().commander is None


def test_commander_priorities_defaults() -> None:
    priorities = CommanderPriorities()
    assert priorities.posture == "neutral"
    assert priorities.strength == "soft"
    assert priorities.target_room is None
    assert priorities.allow_witnessed_kill is False
    assert priorities.as_of_tick == 0


def test_commander_priorities_is_frozen() -> None:
    priorities = CommanderPriorities(target_room="electrical")

    with pytest.raises(Exception):
        priorities.target_room = "medbay"


def test_sanitize_priorities_defaults_strength_to_soft() -> None:
    priorities = sanitize_priorities({}, set(), set(), as_of_tick=1)

    assert priorities.strength == "soft"


def test_sanitize_priorities_accepts_hard_strength() -> None:
    priorities = sanitize_priorities({"strength": "hard"}, set(), set(), as_of_tick=1)

    assert priorities.strength == "hard"


def test_sanitize_priorities_rejects_invalid_strength() -> None:
    priorities = sanitize_priorities({"strength": "maximum"}, set(), set(), as_of_tick=1)

    assert priorities.strength == "soft"
