"""Normalize ``EpisodeResult.game_results`` to the per-slot results_schema shape.

The container round-runner currently forwards a metadata stub
(``episode_id`` / ``job_id`` / ``replay_url``) instead of the game-written
results artifact. Skill metrics need the seat-indexed arrays from that artifact
(``vote_players``, ``kills``, ``tasks``, ``imposter``, ...).

This module unwraps common nesting and detects whether the payload already
contains results_schema arrays so ``decision.py`` can score episodes reliably.
"""

from __future__ import annotations

from typing import Any

_SKILL_ARRAY_KEYS = ("vote_players", "kills", "tasks", "imposter")
_METADATA_STUB_KEYS = frozenset({"episode_id", "job_id", "replay_url"})


def _is_list_of_numbers(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, (int, float)) for x in value)


def has_results_schema_arrays(game_results: dict[str, Any]) -> bool:
    """True when ``game_results`` already looks like the coworld results_schema."""
    if not _is_list_of_numbers(game_results.get("scores")):
        return False
    return any(_is_list_of_numbers(game_results.get(key)) for key in _SKILL_ARRAY_KEYS)


def is_metadata_stub(game_results: dict[str, Any]) -> bool:
    """True when the platform forwarded only episode/job metadata."""
    if has_results_schema_arrays(game_results):
        return False
    keys = set(game_results)
    return keys and keys <= _METADATA_STUB_KEYS


def _unwrap_candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if has_results_schema_arrays(value):
        return value
    nested = value.get("results")
    if isinstance(nested, dict) and has_results_schema_arrays(nested):
        return nested
    coworld = value.get("coworld")
    if isinstance(coworld, dict):
        nested = coworld.get("results")
        if isinstance(nested, dict) and has_results_schema_arrays(nested):
            return nested
    attributes = value.get("attributes")
    if isinstance(attributes, dict):
        coworld = attributes.get("coworld")
        if isinstance(coworld, dict):
            nested = coworld.get("results")
            if isinstance(nested, dict) and has_results_schema_arrays(nested):
                return nested
    return None


def coerce_results_schema(game_results: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a results_schema dict suitable for ``decision.evaluate_entrants``."""
    if game_results is None:
        return None
    if has_results_schema_arrays(game_results):
        return game_results
    unwrapped = _unwrap_candidate(game_results)
    if unwrapped is not None:
        return unwrapped
    return game_results
