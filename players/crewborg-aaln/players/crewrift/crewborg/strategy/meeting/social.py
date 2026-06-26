"""Who-sus'd-who: parse meeting chat into (target, stance) mentions.

Meeting chat is the only social evidence channel: players accuse ("red vented in
electrical"), defend ("blue was with me"), and bandwagon. This module turns one
chat line into structured ``(target_color, stance)`` pairs that ``update_belief``
folds into ``belief.accusations`` — the episode-persistent accusation graph the
suspicion model and the meeting LLM context consume.

Pure text → tuples: no crewborg imports, so :mod:`...types` can import the parser
without a cycle. Heuristics, deliberately simple:

- A *mention* is a player color name on a word boundary, matched longest-first so
  "light blue" never double-counts as "blue".
- The line is split into clauses (on ``,;.!?``) and each clause is classified
  independently, so "red is sus, blue is clear" yields an accusation *and* a
  defense.
- A clause with a defend keyword ("clear", "with me", "innocent", …) defends its
  mentions; otherwise a mention is an accusation — naming someone in a meeting is
  pointing at them ("red?", "vote red", "saw red vent" all accuse).
- The speaker's own color is never a target (self-references are alibis, not
  accusations of another player).

Beyond the (target, stance) pairs, :func:`has_evidence_context` classifies the
*format* of an accusation: whether the line carries any evidence wording (a body,
a vent, a sighting…) or is a bare assertion. The 2026-06-11 truecrew eval showed
chat-format-level signal quality is real — bare ``"<color> sus"`` lines were a
0/185 imposter-naming disinfo channel, while ``"body in <room> sus <color>"``
reports ran 84% accurate — so downstream consumers (suspicion, the vote policy's
pile-on gate) treat evidence-backed and bare accusations very differently.
"""

from __future__ import annotations

import re
from typing import Literal

Stance = Literal["accuse", "defend"]

# The 16 Crewrift player colors (PlayerColorNames; AGENTS.md §2), ordered
# longest-first so multi-word names win the regex alternation.
COLOR_NAMES: tuple[str, ...] = (
    "light blue",
    "pale blue",
    "dark brown",
    "dark teal",
    "dark navy",
    "orange",
    "yellow",
    "brown",
    "green",
    "black",
    "white",
    "lime",
    "pink",
    "gray",
    "blue",
    "red",
)

_COLOR_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in COLOR_NAMES) + r")\b")
_CLAUSE_RE = re.compile(r"[,;.!?]+")

# A clause containing any of these defends its mentioned colors; everything else
# that names a color reads as an accusation.
# Evidence wording: a line containing any of these ties its accusation to a
# concrete observation (a body, a vent, a sighting, a chase…) rather than a bare
# assertion. Matched on the whole line, lowercased, substring-style (so "saw"
# also matches "i saw him"). Deliberately generous — misclassifying a bare line
# as evidence only restores the old (pre-gate) behavior for that line.
_EVIDENCE_KEYWORDS = (
    "body",
    "bodies",
    "vent",
    "saw",
    "seen",
    "watch",
    "follow",
    "chas",  # chase / chasing / chased
    "kill",
    "stab",
    "report",
    "camp",
    "stalk",
)

_DEFEND_KEYWORDS = (
    "clear",
    "safe",
    "innocent",
    "with me",
    "was with",
    "not sus",
    "isnt sus",
    "isn't sus",
    "vouch",
    "trust",
    "cant be",
    "can't be",
    "cannot be",
    "not the",
    "didnt",
    "didn't",
)


def parse_stances(speaker_color: str | None, text: str) -> list[tuple[str, Stance]]:
    """Parse one chat line into ``(target_color, stance)`` pairs.

    The speaker's own color is excluded; duplicate (target, stance) pairs within
    one line collapse to one.
    """

    lowered = text.lower()
    pairs: list[tuple[str, Stance]] = []
    seen: set[tuple[str, Stance]] = set()
    for clause in _CLAUSE_RE.split(lowered):
        stance: Stance = "defend" if any(k in clause for k in _DEFEND_KEYWORDS) else "accuse"
        for match in _COLOR_RE.finditer(clause):
            target = match.group(1)
            if target == speaker_color:
                continue
            if (target, stance) not in seen:
                seen.add((target, stance))
                pairs.append((target, stance))
    return pairs


def has_evidence_context(text: str) -> bool:
    """Whether one chat line ties its claims to a concrete observation.

    ``True`` for lines with evidence wording ("body in storage sus red",
    "saw blue vent"); ``False`` for bare assertions ("red sus", "vote red").
    A bare accusation is not merely weak evidence — against truecrew:v14 it was
    a near-perfect *innocence* marker for the named color (0/185 named a real
    imposter) and a tell that the speaker is steering the meeting.
    """

    lowered = text.lower()
    return any(keyword in lowered for keyword in _EVIDENCE_KEYWORDS)
