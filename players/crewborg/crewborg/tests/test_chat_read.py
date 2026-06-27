"""Meeting chat-NLP: dependency-parse accusation detection + graceful no-signal."""

from __future__ import annotations

import pytest

from crewborg.perception.entities import VotingState
from crewborg.strategy.meeting import chat_nlp, chat_read
from crewborg.types import Belief, ChatEvent, PlayerRecord

_COLORS = ("red", "blue", "green", "yellow", "orange", "purple")


@pytest.fixture(scope="module")
def nlp_model():
    """Load the real model once and inject it (bypassing the async loader)."""

    import spacy

    model = spacy.load("en_core_web_sm", disable=["ner"])
    saved = chat_nlp._model
    chat_nlp._model = model
    yield model
    chat_nlp._model = saved


def _belief_with_chat(messages, *, self_color="orange", teammates=()) -> Belief:
    belief = Belief(self_role="imposter", teammate_colors=set(teammates))
    belief.voting = VotingState(self_marker_color=self_color)
    for color in _COLORS:
        belief.roster[color] = PlayerRecord(color=color, life_status="alive")
    belief.chat_log = [ChatEvent(tick=i, speaker_color=s, text=t) for i, (s, t) in enumerate(messages)]
    return belief


def test_no_model_means_no_chat_signal() -> None:
    # Without a loaded model (disabled / still loading), there is no chat signal at all.
    saved = chat_nlp._model
    chat_nlp._model = None
    try:
        belief = _belief_with_chat([("blue", "red sus")])
        assert chat_read.chat_accusers(belief) == {}
    finally:
        chat_nlp._model = saved


def test_a_plain_accusation_is_detected(nlp_model) -> None:
    assert chat_read.chat_accusers(_belief_with_chat([("blue", "red sus")])) == {"red": 1}


def test_negated_accusation_is_not_counted(nlp_model) -> None:
    assert chat_read.chat_accusers(_belief_with_chat([("blue", "red isn't sus")])) == {}
    assert chat_read.chat_accusers(_belief_with_chat([("blue", "i don't think red did it")])) == {}


def test_non_accusation_chatter_is_filtered_by_the_gate(nlp_model) -> None:
    # No color + sus-cue ⇒ the keyword gate skips it before spaCy.
    assert chat_read.chat_accusers(_belief_with_chat([("blue", "gg everyone nice game")])) == {}
