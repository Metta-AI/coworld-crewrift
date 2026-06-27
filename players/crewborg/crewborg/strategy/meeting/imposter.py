"""Imposter meeting tactics: who to bandwagon onto (design §10.4).

The imposter never targets a teammate. When it has no strong real deflection of its
own (``top_suspect`` over non-teammates, see ``suspicion.py``), it waits and watches
for a crewmate to take **heat** — a vote cast against them (the reliable signal, read
from the vote tally) or a chat accusation (the additive ``chat_read`` signal) — then
piles on. This module turns those signals into a single bandwagon target.

The hard invariant here is **never out a teammate**: ``bandwagon_target`` excludes our own
teammate colors (and self, the dead, and skip) before scoring, so the imposter can only ever
pile onto a *crewmate*. The downstream accusation it then voices is fabricated through the
same template a real one uses (``accusation.fabricate_accusation``), so bandwagoning isn't a
role tell.

Collaborators
-------------
Relies on: ``types.Belief`` only — ``belief.voting`` (candidates / dots / self marker) for
  the vote tally and ``belief.teammate_colors`` for the teammate exclusion. The chat-accuser
  counts are passed in (computed by ``chat_read.chat_accusers``), not imported here.
Used by: ``modes.attend_meeting._decide_imposter`` — ``votes_against`` (trace) and
  ``bandwagon_target`` (who to pile onto). ``chat_read`` shares the ``chat_accusers`` name.

Modifying this file: preserve the teammate/self/dead/skip exclusions in ``bandwagon_target`` —
they are the "never out a teammate" guard. The weights below are tunable, but a cast vote
should stay weighted over a lone chat accusation (votes are the reliable signal).
"""

from __future__ import annotations

from crewborg.types import Belief

# Heat weights: a cast vote is a stronger, more reliable "this crewmate is in trouble" signal
# than a single chat accusation, so it counts double.
VOTE_WEIGHT = 2
CHAT_WEIGHT = 1


def votes_against(belief: Belief) -> dict[str, int]:
    """Count of votes cast against each candidate color, by players other than us
    (skip votes and our own ballot excluded)."""

    candidates = belief.voting.candidates
    slot_to_color = {c.slot: c.color for c in candidates}
    self_color = belief.voting.self_marker_color
    self_slot = next((c.slot for c in candidates if c.color == self_color), None)

    tally: dict[str, int] = {}
    for dot in belief.voting.dots:
        if dot.is_skip or dot.voter == self_slot:
            continue
        color = slot_to_color.get(dot.target)
        if color is not None:
            tally[color] = tally.get(color, 0) + 1
    return tally


def bandwagon_target(belief: Belief, chat_accusers: dict[str, int] | None = None) -> str | None:
    """The non-teammate crewmate under the most heat that we can pile onto, or ``None``.

    Heat = votes·``VOTE_WEIGHT`` + distinct chat accusers·``CHAT_WEIGHT``. Excludes
    teammates, self, the dead, and skip. Any heat at all makes a crewmate eligible
    ("someone has sussed or voted for them"); the most-heated one wins ties by votes.
    """

    accusers = chat_accusers or {}
    tally = votes_against(belief)
    self_color = belief.voting.self_marker_color
    alive_colors = {c.color for c in belief.voting.candidates if c.alive}

    best: tuple[str, int] | None = None
    for color in set(tally) | set(accusers):
        if color in belief.teammate_colors or color == self_color:
            continue
        if alive_colors and color not in alive_colors:
            continue  # can't eject the dead (skip the filter when the grid is unknown)
        heat = tally.get(color, 0) * VOTE_WEIGHT + accusers.get(color, 0) * CHAT_WEIGHT
        if heat <= 0:
            continue
        if best is None or heat > best[1]:
            best = (color, heat)
    return best[0] if best is not None else None
