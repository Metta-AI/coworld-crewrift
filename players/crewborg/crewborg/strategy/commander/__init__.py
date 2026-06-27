"""Gameplay-commander package: the opt-in, gated-off-by-default LLM advisor.

This package is crewborg's *gameplay commander* — a background LLM that writes
sticky high-level **priorities** (``belief.commander``) which the deterministic
modes optionally READ to bias execution. It is distinct from the meeting chatter
(the separate LLM that speaks/votes during meetings). The commander never selects
modes, never presses buttons, and never touches live belief from its thread; it
only publishes a sanitized ``CommanderPriorities`` payload that modes consult at
discretionary ranking points.

Gating (the load-bearing invariant): the whole feature is OFF unless
``CREWBORG_LLM_COMMANDER`` is truthy. When off, ``CommanderStrategy`` short-circuits
to pure ``RuleBasedStrategy`` selection, ``belief.commander`` stays ``None``, and
play is byte-identical to the deterministic agent. "Off = inert" is enforced at
every layer (client construction, the strategy wrapper, and the per-field
sanitizer), and is what makes it safe to ship this code in the submitted policy.

Layout:
  - ``llm.py`` — backend selection (Anthropic direct vs Bedrock sidecar) + client.
  - ``worker.py`` — background daemon thread that runs the LLM calls.
  - ``context.py`` — serialize belief into the compact JSON state the LLM sees.
  - ``prompts.py`` — role-specific system prompts (crewmate / imposter).
  - ``schema.py`` — sanitize raw LLM JSON into a safe ``CommanderPriorities``.
  - ``strategy.py`` — ``CommanderStrategy`` wrapper + ``apply_commander_inferences``.
  - ``bias.py`` — ``commander_of`` accessor (with TTL) and the consumption helpers.
  - ``trace.py`` — thread-safe telemetry handoff from the worker to the tracer.
"""
