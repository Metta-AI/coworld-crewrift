"""Read meeting chat for *who is being accused* (design §10.5).

The imposter's reactive bandwagon (§10.4) wants to know which crewmates other players
are sussing in chat, to pile on before it hardens into a vote. Free-form chat makes
this hard, but the target vocabulary is a **closed set of colors**, which we exploit:

1. **Keyword pre-gate** (cheap) — a message is only worth parsing if it names a color
   *and* carries a sus cue. Most chatter is filtered out here.
2. **Dependency-parse negation scope** (spaCy, `chat_nlp`) — the real value. A crude
   "is a negation word present?" guard mishandles ``"red isn't sus"`` vs
   ``"red is sus not blue"``; a dependency parse tracks which clause the negation
   governs, handles contrastive negation, the victim-vs-suspect flip
   (``"when red died"`` ⇒ red is the victim), and defense phrasings.

If the model is disabled or still loading (`chat_nlp.get_model()` is ``None``), there
is **no** chat signal — we deliberately do *not* fall back to crude keyword matching
(its false positives are exactly what this layer exists to avoid); the bandwagon then
rests on the reliable vote tally alone.

Collaborators
-------------
Relies on:
  - ``chat_nlp.get_model`` — the spaCy pipeline; ``None`` ⇒ this module returns ``{}``.
  - ``types.Belief`` — ``chat_log`` (messages), ``roster`` (the closed color set),
    ``teammate_colors`` + ``voting.self_marker_color`` (excluded from the result).
Used by: ``modes.attend_meeting._chat_accusers`` (passing a per-meeting cache), feeding
  ``imposter.bandwagon_target`` as the additive chat-heat signal.

Modifying this file: this only *reads* opponents' chat for accusations — never our own
(filtered out), and the result excludes teammates and self. Returning ``{}`` when the model
isn't ready is intended (graceful no-signal), not an error path. The dependency-parse
negation scope (``_extract`` / ``_negated``) is the whole value over keyword matching; don't
regress it to a bare keyword check.
"""

from __future__ import annotations

from typing import Any

from crewborg.strategy.meeting import chat_nlp
from crewborg.types import Belief

# Cues that an utterance is an accusation. Closed, tunable; inflections included so we
# can match on the lowercase token without depending on the lemmatizer.
SUS_WORDS = frozenset({
    "sus", "suspicious", "vent", "vented", "venting", "vents", "kill", "killed", "kills",
    "body", "dead", "died", "vote", "votes", "voting", "imp", "imposter", "impostor",
    "fake", "faking", "faked", "follow", "following", "followed", "lying", "lie", "lied",
    "did", "saw", "report",
})
# Negation cues — checked against the dependency tree, not bare presence.
NEG_WORDS = frozenset({"not", "n't", "no", "never", "isnt", "dont", "doesnt", "cant", "aint"})
# Defense/clearing cues that govern a color's clause flip it to "not accused".
DEFENSE_WORDS = frozenset({"innocent", "clear", "cleared", "vouch", "trust", "safe", "good", "sure", "with"})
# A victim cue adjacent to a color marks it as the *victim*, not the suspect.
VICTIM_WORDS = frozenset({"died", "dead", "body", "killed"})


def chat_accusers(belief: Belief, *, cache: dict[str, set[str]] | None = None) -> dict[str, int]:
    """Per non-teammate color, the count of *distinct other speakers* who accused them
    in chat. Empty when the NLP model isn't available. ``cache`` (caller-owned, reset
    each meeting) memoizes per-message parses so we don't re-parse every tick."""

    nlp = chat_nlp.get_model()
    if nlp is None:
        return {}

    colors = set(belief.roster)
    self_color = belief.voting.self_marker_color
    cache = cache if cache is not None else {}

    by_color: dict[str, set[str | None]] = {}
    for event in belief.chat_log:
        if event.speaker_color is not None and event.speaker_color == self_color:
            continue  # our own chat isn't a signal to bandwagon on
        for color in _accused_for(event.text, colors, nlp, cache):
            by_color.setdefault(color, set()).add(event.speaker_color)

    return {
        color: len(speakers)
        for color, speakers in by_color.items()
        if color not in belief.teammate_colors and color != self_color
    }


def _accused_for(text: str, colors: set[str], nlp: Any, cache: dict[str, set[str]]) -> set[str]:
    """The set of colors a single message accuses, memoized by exact text. Cheap-gates first
    (``_gate``) and only runs the spaCy parse (``_extract``) on messages that pass."""

    if text in cache:
        return cache[text]
    accused = _extract(nlp, text, colors) if _gate(text, colors) else set()
    cache[text] = accused
    return accused


def _gate(text: str, colors: set[str]) -> bool:
    """Cheap filter: the message names a color and carries a sus cue — else skip spaCy."""

    tokens = set(text.lower().replace(",", " ").split())
    return bool(tokens & colors) and bool(tokens & SUS_WORDS)


def _extract(nlp: Any, text: str, colors: set[str]) -> set[str]:
    """The colors this message *accuses*, with dependency-based negation scope.

    For each color token: gather its clause (the head-chain's subtrees); it's accused
    iff that clause carries a sus cue, the color isn't an adjacent victim, and the
    clause isn't negated/defended.
    """

    doc = nlp(text)
    accused: set[str] = set()
    for tok in doc:
        if tok.lower_ not in colors:
            continue
        chain = _head_chain(tok)
        clause = {t for c in chain for t in c.subtree}
        has_cue = any(t.lower_ in SUS_WORDS for t in clause)
        if not has_cue:
            continue
        is_victim = any(t.lower_ in VICTIM_WORDS and abs(t.i - tok.i) <= 2 for t in doc)
        if is_victim:
            continue
        if not _negated(doc, chain, clause):
            accused.add(tok.lower_)
    return accused


def _head_chain(tok: Any, depth: int = 6) -> list[Any]:
    """The color token plus its syntactic-head ancestors (up to ``depth`` hops, stopping at
    the root), i.e. the governing clause path used to scope cues and negation to that color."""

    chain = [tok]
    head = tok
    for _ in range(depth):
        if head.head == head:
            break
        head = head.head
        chain.append(head)
    return chain


def _negated(doc: Any, chain: list[Any], clause: set[Any]) -> bool:
    """True if the color's clause is negated or defended — a dependency ``neg`` child on the
    head chain, a negation word governed by the chain, or a defense/clearing word heading or
    inside the clause (e.g. ``"red isn't sus"`` / ``"red is cleared"``)."""

    chainset = set(chain)
    return (
        any(child.dep_ == "neg" for c in chain for child in c.children)
        or any(t.lower_ in NEG_WORDS and t.head in chainset for t in doc)
        or any(t.lower_ in DEFENSE_WORDS and (t.head in chainset or t in clause) for t in doc)
    )
