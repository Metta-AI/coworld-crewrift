"""Offline batch warehouse for Crewrift episode events.

Runs the per-episode event extraction from ``crewrift-event-reporter`` across an
arbitrarily large batch of episodes, re-keys every event from slot to policy
identity and role, and collates the result into a policy-indexed, partitioned
Parquet dataset (a star schema: ``events`` fact + ``episode_players`` dimension).
"""

from .warehouse import build_warehouse

__all__ = ["build_warehouse"]
