"""Crewborg strategy layer: belief → suspicion → mode selection (design §10).

The top of the cognitive stack. Downstream of perception/belief, it runs three
knowledge-layer passes each tick — ``update_event_log`` (durative per-player
observations), ``update_social_evidence`` (cumulative meeting/vote/chat counters), then
``update_suspicion`` (the Bayesian P(imposter) posterior) — and exposes
``RuleBasedStrategy``, the deterministic per-tick mode SELECTOR (§10 priority orders).
``build_runtime`` (``crewborg.__init__``) composes the passes in that order and
wraps the selector in ``SynchronousStrategyRunner``.

This re-exports only the four pipeline entry points. The strategy *helpers* —
``opportunity`` (kill-window/witness gates), ``occupancy`` (frame predicates),
``trajectory`` / ``path_prediction`` (intercept / route projection), and the
``commander`` / ``meeting`` subpackages — are imported directly from their modules by
the callers that need them.
"""

from crewborg.strategy.event_log import update_event_log
from crewborg.strategy.rule_based import RuleBasedStrategy
from crewborg.strategy.social_evidence import update_social_evidence
from crewborg.strategy.suspicion import update_suspicion

__all__ = ["RuleBasedStrategy", "update_event_log", "update_social_evidence", "update_suspicion"]
