"""Sprite-v1 protocol constants for the Crewrift ``/player`` stream.

All values verified against the game source on 2026-05-29
(``~/coding/games/coworld-crewrift/src/crewrift/{sim,global}.nim``). These are
the perception contract — re-check the source if perception misbehaves.
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
# Game-over interstitial roster icons (global.nim ProtocolGameOverIconObjectBase):
# one "player <color> <facing>" icon per player, paired by row with an "IMP"/"CREW"
# text item — the end-of-game ground-truth role census by color.
GAMEOVER_ICON_OBJECT_BASE = 9700
# Meeting-call interstitial icons (global.nim ProtocolMeetingIconObjectBase, added
# upstream 2026-06-10 "add meeting call interstitial"): base + 0 is the CALLER's
# "player <color> <facing>" icon; base + 1 is either the reported "body <color>"
# sprite (report-triggered) or the "meeting button" sprite (button-triggered).
MEETING_ICON_OBJECT_BASE = 9800

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
# The meeting-call interstitial's emergency-button icon (button-triggered
# meetings; global.nim init, upstream 2026-06-10).
LABEL_MEETING_BUTTON = "meeting button"

# Label prefixes for entity sprites (suffix carries the color / state).
PREFIX_PROGRESS_BAR = "progress bar "  # "progress bar 45%"
PREFIX_TASK_COUNTER = "task counter "  # "task counter 7"
# Per-tick server tick marker (upstream 2026-06-10 "add tick log marker"): an
# invisible 1x1 sprite (id 5016, object 5016) whose label is "tick <N>" with N =
# the server's sim.tickCount — the same tick counter .bitreplay files use, so this
# is the join key between crewborg's trace.db and the server replay.
PREFIX_SERVER_TICK = "tick "
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
PHASE_TEXT_DIED = "DIED"  # second line of the no-ejection vote result (with "NO ONE")
PHASE_TEXT_WAS_KILLED = "WAS KILLED"
PHASE_TEXT_DRAW = "DRAW"
PHASE_TEXT_CREW_WINS = "CREW WINS"
PHASE_TEXT_IMPS_WIN = "IMPS WIN"
# Pre-game info screen title (upstream 2026-06-10 "add game info interstitial"):
# a new GameInfo phase between the lobby countdown and role reveal, whose text
# items expose the live game config (see the GAME_INFO_* prefixes below).
PHASE_TEXT_GAME_INFO = "GAME INFO"

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
        PHASE_TEXT_DIED,
        PHASE_TEXT_WAS_KILLED,
        PHASE_TEXT_DRAW,
        PHASE_TEXT_CREW_WINS,
        PHASE_TEXT_IMPS_WIN,
        PHASE_TEXT_GAME_INFO,
    }
)

# Game-info screen settings lines (global.nim gameInfoTextLines): each is a text
# sprite whose label encodes one live config value. "GAME TIMER NONE" appears when
# maxTicks is 0 (no limit).
GAME_INFO_PREFIX_KILL_COOLDOWN = "KILL COOLDOWN "  # "KILL COOLDOWN 500T"
GAME_INFO_PREFIX_TASKS = "TASKS "  # "TASKS 8 EACH"
GAME_INFO_PREFIX_VOTE_TIMER = "VOTE TIMER "  # "VOTE TIMER 1200T"
GAME_INFO_PREFIX_GAME_TIMER = "GAME TIMER "  # "GAME TIMER 10000T" / "GAME TIMER NONE"

# Meeting-call interstitial text lines (global.nim meetingCallLines, upstream
# 2026-06-10): "<Color> reported" + "<Color>'s body" | "a body", or "<Color>
# pressed" + "the button", or "<Color> called" + "a meeting". <Color> is the
# caller's color name with the first letter capitalized ("Light blue"), or
# "Someone" when the caller has left the game. The caller/body icons (object ids
# MEETING_ICON_OBJECT_BASE/+1) are the authoritative signal; these texts are the
# fallback for the icon-less "Someone" case.
MEETING_TEXT_SUFFIX_REPORTED = " reported"
MEETING_TEXT_SUFFIX_PRESSED = " pressed"
MEETING_TEXT_SUFFIX_CALLED = " called"
MEETING_TEXT_THE_BUTTON = "the button"
MEETING_TEXT_A_MEETING = "a meeting"
MEETING_TEXT_A_BODY = "a body"
MEETING_TEXT_BODY_SUFFIX = "'s body"

# Game-over roster role texts (global.nim interstitialTextItems GameOver branch):
# one "IMP"/"CREW" per player, row-paired with the GAMEOVER_ICON_OBJECT_BASE icon.
GAMEOVER_TEXT_IMP = "IMP"
GAMEOVER_TEXT_CREW = "CREW"
