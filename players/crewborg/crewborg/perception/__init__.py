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
"""

from crewborg.perception.decoder import SpriteProtocolError, apply_message
from crewborg.perception.resolve import resolve_scene

__all__ = ["SpriteProtocolError", "apply_message", "resolve_scene"]
