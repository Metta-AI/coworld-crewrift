"""ASGI entrypoint for the Crewrift Prime advanced-skill commissioner.

Importing ``crewrift_prime_skill_commissioner`` runs its module-level
``register_commissioner(COMMISSIONER_KEY, ...)`` so the key is in the stock
registry before ``commissioner_app`` resolves it. ``commissioner_app`` reads
COMMISSIONER_KEY from the environment (set to ``crewrift_prime_skill`` in the
image).
"""

from __future__ import annotations

# Side effect: registers the "crewrift_prime_skill" commissioner key.
import crewrift_prime_skill_commissioner  # noqa: F401

from commissioners.common.app import commissioner_app

app = commissioner_app()
