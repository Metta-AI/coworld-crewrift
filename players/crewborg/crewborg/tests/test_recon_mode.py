"""Recon mode tests (modes/recon.py).

Recon pre-positions on the most-recently-seen crewmate in the short window before the
kill comes off cooldown, so a victim is in hand the instant we can kill.
"""

from __future__ import annotations

from crewborg.modes.recon import ReconMode
from crewborg.strategy.opportunity import recon_window
from crewborg.types import ActionState, Belief, PlayerRecord


def _imposter() -> Belief:
    b = Belief(phase="Playing", self_role="imposter", self_world_x=100, self_world_y=100, last_tick=50)
    b.teammate_colors = {"red"}  # crewborg's partner
    return b


def _seen(belief: Belief, color: str, xy, tick) -> PlayerRecord:
    rec = PlayerRecord(color=color, world_x=xy[0], world_y=xy[1], last_seen_tick=tick, life_status="alive")
    belief.roster[color] = rec
    return rec


def test_recon_beelines_to_the_most_recently_seen_crewmate() -> None:
    b = _imposter()
    _seen(b, "green", (200, 60), tick=20)
    _seen(b, "blue", (300, 80), tick=45)  # more recently seen ⇒ the target
    intent = ReconMode().decide(b, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (300, 80)


def test_recon_ignores_the_teammate_imposter() -> None:
    b = _imposter()
    _seen(b, "red", (300, 80), tick=49)   # teammate, most recent — must be skipped
    _seen(b, "green", (200, 60), tick=30)  # the only real crewmate
    intent = ReconMode().decide(b, ActionState())
    assert intent.point == (200, 60)


def test_recon_window_default_and_env(monkeypatch) -> None:
    monkeypatch.delenv("CREWBORG_RECON_WINDOW", raising=False)
    assert recon_window() == 100
    monkeypatch.setenv("CREWBORG_RECON_WINDOW", "250")
    assert recon_window() == 250
    monkeypatch.setenv("CREWBORG_RECON_WINDOW", "garbage")
    assert recon_window() == 100  # invalid falls back to default
