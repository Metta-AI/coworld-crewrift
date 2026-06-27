"""Sprite-v1 protocol constants for the Crewrift ``/player`` stream.

The authoritative perception contract: message-type bytes, object-id bases/limits,
sprite labels and label prefixes, the camera/self/collision coordinate offsets, and
the player palette + phase-text vocabulary. Everything in the perception layer that
decodes or classifies the scene reads these — they are the single place the wire
format is pinned.

All values verified against the game source on 2026-05-29
(``src/crewrift/{sim,global}.nim`` in the ``Metta-AI/coworld-crewrift`` repo). These are
the perception contract — re-check the source if perception misbehaves.

Collaborators
-------------
Relies on: nothing — a pure leaf of literals (the import root of the layer).
Used by:
  - ``perception.decoder`` — ``MSG_*`` type bytes, the two decoded labels, the
    world-map object/sprite ids.
  - ``perception.resolve`` — every object-id base/limit, label, prefix, the
    self/collision offsets, ``MAX_PLAYERS``, and ``PHASE_TEXTS``.
  - ``events.py`` / ``types.py`` — ``SCREEN_WIDTH``/``SCREEN_HEIGHT`` and
    ``PLAYER_OBJECT_BASE`` for on-screen tests.
Emits / touches: nothing — module-level constants only.

Modifying this file: these mirror the game's Nim source, not crewborg's
preferences. Change a value only to track a verified source change, and cite the
``sim.nim``/``global.nim`` line (as the existing comments do). The id ranges are
assumed disjoint by ``resolve.py``'s range checks — preserve that when editing bases.
"""

from __future__ import annotations

# Server -> client message type bytes.
MSG_DEFINE_SPRITE = 0x01
MSG_DEFINE_OBJECT = 0x02
MSG_DELETE_OBJECT = 0x03
MSG_CLEAR_OBJECTS = 0x04
MSG_SET_VIEWPORT = 0x05
MSG_DEFINE_LAYER = 0x06

# The world-map object: object id 1 using sprite id 1, placed at (-camX, -camY).
MAP_OBJECT_ID = 1
MAP_SPRITE_ID = 1

# Stable object-id bases (sim.nim: PlayerObjectBase/BodyObjectBase/TaskObjectBase,
# global.nim: SpritePlayerTaskArrowObjectBase/SpritePlayerVoteDotObjectBase).
PLAYER_OBJECT_BASE = 1000
BODY_OBJECT_BASE = 2000
TASK_BUBBLE_OBJECT_BASE = 3000
TASK_ARROW_OBJECT_BASE = 7000
# Role-reveal icons (global.nim:106). During RoleReveal an imposter viewer is
# shown ONLY its teammates' icons here (object id base + slot), using the normal
# "player <color>" sprites — so these reveal the imposter team.
ROLE_ICON_OBJECT_BASE = 9500

# Voting-screen social UI, rendered as labeled sprites only during Voting /
# VoteResult (global.nim:94-104). These id ranges are disjoint from the in-world
# player/body ranges (1000/2000), so the same "player <color>" / "body <color>"
# labels never collide with live-world entities.
#
# Chat text and interstitial/HUD text SHARE the 9000 range (both are
# ProtocolTextObjectBase + index), so chat lines cannot be told apart by id alone.
# Instead we anchor on the chat *icon* range (exclusively chat) and pair each icon
# to the text line at the same screen-y (global.nim:739-810).
CHAT_TEXT_OBJECT_BASE = 9000  # chat line text sprite; label = the raw message text
CHAT_TEXT_OBJECT_LIMIT = 9200
CHAT_ICON_OBJECT_BASE = 9200  # chat speaker icon; "player <color> <facing>" sprite
CHAT_ICON_OBJECT_LIMIT = 9300
# Voting candidate grid: one cell per player, "player <color>" if alive or
# "body <color>" if dead — an authoritative alive/dead census by color, every
# meeting (global.nim:1124-1167). Object id = base + players-seq index.
VOTE_ICON_OBJECT_BASE = 9300
# Vote-result interstitial: a single icon for the ejected player, "player <color>
# <facing>" (global.nim:1257-1280). Absent when the vote skipped (no one ejected).
RESULT_ICON_OBJECT_ID = 9600

VOTE_DOT_OBJECT_BASE = 10100
# Skip votes use a SEPARATE base and the same "vote dot <color>" sprite: object id
# is VOTE_SKIP_DOT_OBJECT_BASE + voter (global.nim:95,1212). Split by id range.
VOTE_SKIP_DOT_OBJECT_BASE = 10400

# sim.nim MaxPlayers. A normal vote dot's object id is
# VOTE_DOT_OBJECT_BASE + target * MAX_PLAYERS + voter (global.nim:1193), so the
# normal range spans VOTE_DOT_OBJECT_BASE .. + MAX_PLAYERS*MAX_PLAYERS.
MAX_PLAYERS = 16

# The /player camera shows a fixed ScreenWidth×ScreenHeight world window (sim.nim;
# 128×128, the same screen the SELF_OFFSET below is derived from). A world point
# (wx, wy) is on-screen iff camera ≤ (wx, wy) < camera + (SCREEN_WIDTH, SCREEN_HEIGHT).
SCREEN_WIDTH = 128
SCREEN_HEIGHT = 128

# Self-world-position offset (design §3.2). Self is the camera center, not an
# object; inverting playerView's camera math (sim.nim ~2879) with SpriteSize=12,
# SpriteDrawOffX/Y=2/8 and a 128×128 screen gives
# self_world = (camera_x + SELF_OFFSET_X, camera_y + SELF_OFFSET_Y).
SELF_OFFSET_X = 60
SELF_OFFSET_Y = 66

# A visible player/body object is *drawn* at (entity.x - SpriteDrawOffX - 1,
# entity.y - SpriteDrawOffY - 1) (global.nim:2376,2403), but the server's
# collision / report / kill point is entity.x/y (CollisionW/H = 1). Add this
# offset to a decoded object world position to recover the collision point, so
# range checks match the server.
ENTITY_COLLISION_DX = 3  # SpriteDrawOffX + 1
ENTITY_COLLISION_DY = 9  # SpriteDrawOffY + 1

# The 16 player color names, in palette order (global.nim PlayerColorNames).
PLAYER_COLOR_NAMES: tuple[str, ...] = (
    "red",
    "orange",
    "yellow",
    "light blue",
    "pink",
    "lime",
    "blue",
    "pale blue",
    "gray",
    "white",
    "dark brown",
    "brown",
    "dark teal",
    "green",
    "dark navy",
    "black",
)

# Fixed sprite labels (global.nim init / per-tick HUD).
LABEL_WALKABILITY = "walkability map"
# The per-player vision overlay: a screen-sized sprite, resent on any camera move,
# whose opaque pixels are occluded and transparent pixels are visible (line of
# sight). Decoded into a per-frame visibility mask (global.nim:2212, sim.nim:2974).
LABEL_SHADOW = "shadow"
LABEL_MAP = "map"
LABEL_TASK_BUBBLE = "task bubble"
LABEL_TASK_ARROW = "task arrow"
LABEL_IMPOSTER_ICON = "imposter icon"
LABEL_IMPOSTER_ICON_COOLDOWN = "imposter icon cooldown"
LABEL_GHOST_ICON = "ghost icon"
LABEL_VOTE_CURSOR = "vote cursor"
LABEL_VOTE_SKIP_CURSOR = "vote skip cursor"
LABEL_VOTE_TIMER = "vote timer"

# Label prefixes for entity sprites (suffix carries the color / state).
PREFIX_PROGRESS_BAR = "progress bar "  # "progress bar 45%"
PREFIX_TASK_COUNTER = "task counter "  # "task counter 7"
PREFIX_VOTE_SELF_MARKER = "vote self marker "  # + color
PREFIX_VOTE_DOT = "vote dot "  # + color
PREFIX_PLAYER = "player "  # "player <color> left|right"
PREFIX_GHOST = "ghost "  # "ghost <color> left|right"
PREFIX_BODY = "body "  # "body <color>"

# Interstitial phase / result text (global.nim interstitialTextItems). Read the
# game phase from which of these appears.
PHASE_TEXT_WAITING = "WAITING"
PHASE_TEXT_NEED_MORE = "NEED MORE!"
PHASE_TEXT_STARTING = "STARTING"
PHASE_TEXT_IMPS_REVEAL = "IMPS"
PHASE_TEXT_CREWMATE_REVEAL = "CREWMATE"
PHASE_TEXT_SKIP = "SKIP"
PHASE_TEXT_NO_ONE = "NO ONE"
PHASE_TEXT_WAS_KILLED = "WAS KILLED"
PHASE_TEXT_DRAW = "DRAW"
PHASE_TEXT_CREW_WINS = "CREW WINS"
PHASE_TEXT_IMPS_WIN = "IMPS WIN"

# The full set of interstitial phase/result texts, for membership tests.
PHASE_TEXTS: frozenset[str] = frozenset(
    {
        PHASE_TEXT_WAITING,
        PHASE_TEXT_NEED_MORE,
        PHASE_TEXT_STARTING,
        PHASE_TEXT_IMPS_REVEAL,
        PHASE_TEXT_CREWMATE_REVEAL,
        PHASE_TEXT_SKIP,
        PHASE_TEXT_NO_ONE,
        PHASE_TEXT_WAS_KILLED,
        PHASE_TEXT_DRAW,
        PHASE_TEXT_CREW_WINS,
        PHASE_TEXT_IMPS_WIN,
    }
)
