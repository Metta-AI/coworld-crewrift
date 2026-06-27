"""Commander JSON-sanitization trust-boundary guard (strategy/commander/schema.py).

``sanitize_priorities`` validates the commander LLM's raw JSON against the legal
gameplay state: illegal values (unknown rooms/players, bad postures) are coerced
to safe defaults, and the DANGER levers (e.g. allow_witnessed_kill, skip_evade)
are gated on an explicit ``danger_reason``. A silent regression here would let
malformed or unjustified LLM output drive real gameplay actions.
"""

from __future__ import annotations

from crewborg.strategy.commander.schema import sanitize_priorities

LEGAL_ROOMS = {"electrical", "medbay"}
LEGAL_PLAYERS = {"red", "blue"}


def test_unknown_room_dropped() -> None:
    priorities = sanitize_priorities({"target_room": "atlantis"}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.target_room is None
    assert priorities.as_of_tick == 5


def test_danger_without_reason_dropped() -> None:
    priorities = sanitize_priorities({"allow_witnessed_kill": True}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.allow_witnessed_kill is False
    assert priorities.danger_reason is None
