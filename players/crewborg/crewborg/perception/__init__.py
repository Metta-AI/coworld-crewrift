"""Crewborg perception: the Sprite-v1 scene decoder and object resolution.

Two stages (design §3-§4):

- :mod:`.decoder` maintains the three retained tables (Layers/Sprites/Objects),
  recovers the camera, and decodes the walkability mask from incoming binary
  Sprite-v1 messages.
- :mod:`.resolve` joins objects to their sprite labels, converts camera-relative
  coordinates to world coordinates, and classifies entities by (label, id range)
  into a :class:`~.entities.ResolvedScene`.

This is structured-scene maintenance, not computer vision: the only image decode
is the ``walkability map`` alpha channel.

Collaborators
-------------
Relies on: ``.decoder`` (``apply_message`` / ``SpriteProtocolError``) and
  ``.resolve`` (``resolve_scene``); the entities, tables, and constants are imported
  directly from their submodules by callers, not re-exported here.
Used by:
  - ``coworld.scene.SceneState`` imports ``apply_message`` for the decode path.
  - ``types.py`` imports ``resolve_scene`` for the resolve path.
Emits / touches: nothing — this is a re-export surface (``__all__``).

Modifying this file: keep ``__all__`` to the stable layer entry points
(``apply_message`` / ``resolve_scene`` / ``SpriteProtocolError``). Submodule
internals (entities, constants, tables) are imported by their full path elsewhere;
don't widen the public surface without reason.
"""

from crewborg.perception.decoder import SpriteProtocolError, apply_message
from crewborg.perception.resolve import resolve_scene

__all__ = ["SpriteProtocolError", "apply_message", "resolve_scene"]
