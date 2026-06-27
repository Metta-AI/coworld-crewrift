"""Parse one expanded-replay JSONL stream into a structured Game.

Stage B→C glue of the suspicion-learning pipeline (design §4–§5): consumes the
`expand_replay --format jsonl --snapshot-every N` output and produces the typed
pieces the feature extractor needs — players/roles, sampled positions, visibility
intervals, kills/bodies/ejections, meetings with votes, chats, task completions,
and map geometry. Pure parsing: no feature logic here.
"""

from __future__ import annotations

import bisect
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlayerInfo:
    slot: int
    name: str       # address, e.g. "RowDaBoat" (results.json naming, may carry " (2)")
    color: str
    role: str       # "crew" | "imposter"


@dataclass
class StateSample:
    tick: int
    x: int
    y: int
    room: str
    alive: bool
    connected: bool


@dataclass
class Kill:
    tick: int
    killer_slot: int
    victim_slot: int


@dataclass
class BodySighting:
    """A body's existence window and position (from body events + body_state rows)."""

    key: str                 # "slot:x:y" — stable per body instance
    victim_slot: int
    x: int
    y: int
    room: str
    spawn_tick: int


@dataclass
class Vote:
    tick: int
    voter_slot: int
    target_slot: int | None   # None = skip


@dataclass
class ChatLine:
    tick: int
    slot: int
    text: str


@dataclass
class Meeting:
    call_tick: int            # tick the Voting phase opened (the decision point)
    caller_slot: int          # who reported/buttoned (-1 unknown)
    kind: str                 # "body" | "button"
    votes: list[Vote] = field(default_factory=list)
    chats: list[ChatLine] = field(default_factory=list)
    ejected_slot: int | None = None
    end_tick: int | None = None


@dataclass
class TaskCompletion:
    tick: int
    slot: int
    task: int
    while_dead: bool


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int, margin: int = 0) -> bool:
        return (
            self.x - margin <= px < self.x + self.w + margin
            and self.y - margin <= py < self.y + self.h + margin
        )


@dataclass
class Game:
    episode: str
    config: dict
    players: dict[int, PlayerInfo]
    states: dict[int, list[StateSample]]                      # slot -> tick-sorted samples
    visibility: dict[tuple[int, int], list[tuple[int, int]]]  # (obs, target) -> [(t0, t1)]
    body_visibility: dict[tuple[int, str], list[tuple[int, int]]]  # (obs, body_key) -> [(t0, t1)]
    kills: list[Kill]
    bodies: list[BodySighting]
    ejections: list[tuple[int, int]]                          # (tick, slot)
    meetings: list[Meeting]
    task_completions: list[TaskCompletion]
    vents: list[Rect]
    task_sites: list[Rect]
    tick_count: int
    complete: bool

    # ---- lookups ---------------------------------------------------------

    def state_at(self, slot: int, tick: int) -> StateSample | None:
        """The latest sample at or before `tick` for a slot."""
        samples = self.states.get(slot)
        if not samples:
            return None
        i = bisect.bisect_right([s.tick for s in samples], tick) - 1
        return samples[i] if i >= 0 else None

    def sees(self, observer: int, target: int, tick: int) -> bool:
        """True when an observer's visibility interval covers `tick` for a player."""
        for t0, t1 in self.visibility.get((observer, target), ()):
            if t0 <= tick <= t1:
                return True
        return False

    def sees_body(self, observer: int, body_key: str, tick: int) -> bool:
        for t0, t1 in self.body_visibility.get((observer, body_key), ()):
            if t0 <= tick <= t1:
                return True
        return False

    def sample_ticks(self, slot: int) -> list[int]:
        return [s.tick for s in self.states.get(slot, ())]


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path, encoding="utf-8")


def parse_game(path: Path, episode: str | None = None) -> Game:
    """Parse one expanded JSONL file (optionally .gz) into a Game."""
    players: dict[int, PlayerInfo] = {}
    states: dict[int, list[StateSample]] = {}
    visibility: dict[tuple[int, int], list[tuple[int, int]]] = {}
    body_visibility: dict[tuple[int, str], list[tuple[int, int]]] = {}
    kills: list[Kill] = []
    bodies: list[BodySighting] = []
    body_keys_seen: set[str] = set()
    ejections: list[tuple[int, int]] = []
    meetings: list[Meeting] = []
    task_completions: list[TaskCompletion] = []
    vents: list[Rect] = []
    task_sites: list[Rect] = []
    config: dict = {}
    tick_count = 0
    complete = False
    kill_ticks: dict[tuple[int, int], int] = {}   # (tick, slot) of kill victims

    pending_meeting: Meeting | None = None

    with _open_text(path) as fh:
        for line in fh:
            row = json.loads(line)
            key = row["key"]
            value = row.get("value") or {}
            tick = row["ts"]
            slot = row.get("player", -1)
            tick_count = max(tick_count, tick)

            if key == "player_manifest":
                # address is inside the label "color(address)"
                label = value.get("label", "")
                name = label[label.find("(") + 1 : label.rfind(")")] if "(" in label else label
                players[slot] = PlayerInfo(
                    slot=slot, name=name, color=value.get("color", ""), role=value.get("role", "")
                )
            elif key == "episode_metadata":
                config = value.get("config", {})
            elif key == "map_geometry":
                vents = [Rect(v["x"], v["y"], v["w"], v["h"]) for v in value.get("vents", [])]
                task_sites = [Rect(t["x"], t["y"], t["w"], t["h"]) for t in value.get("tasks", [])]
            elif key == "player_state":
                # player_manifest rows are emitted at join time, BEFORE RoleReveal —
                # their role field is always "crew". The sampled state rows carry the
                # live role, so keep the players map updated from them.
                if slot in players and value.get("role"):
                    players[slot].role = value["role"]
                states.setdefault(slot, []).append(
                    StateSample(
                        tick=tick,
                        x=value["x"],
                        y=value["y"],
                        room=value.get("room", ""),
                        alive=value.get("alive", True),
                        connected=value.get("connected", True),
                    )
                )
            elif key == "player_visible_interval":
                pair = (value["observer_slot"], value["target_slot"])
                visibility.setdefault(pair, []).append((value["tick_start"], value["tick_end"]))
            elif key == "body_visible_interval":
                pair = (value["observer_slot"], value["target_id"].removeprefix("body:"))
                body_visibility.setdefault(pair, []).append((value["tick_start"], value["tick_end"]))
            elif key == "kill":
                kills.append(Kill(tick=tick, killer_slot=slot, victim_slot=value["victim_slot"]))
                kill_ticks[(tick, value["victim_slot"])] = tick
            elif key == "body_state":
                body_key = f"{slot}:{value['x']}:{value['y']}"
                if body_key not in body_keys_seen:
                    body_keys_seen.add(body_key)
                    bodies.append(
                        BodySighting(
                            key=body_key,
                            victim_slot=slot,
                            x=value["x"],
                            y=value["y"],
                            room=value.get("room", ""),
                            spawn_tick=tick,
                        )
                    )
            elif key == "died":
                # bare `died` (no kill attribution) = ejection by vote
                if (tick, slot) not in kill_ticks:
                    ejections.append((tick, slot))
                    if pending_meeting is not None and pending_meeting.end_tick is None:
                        pending_meeting.ejected_slot = slot
            elif key in ("vote_called_body", "vote_called_button"):
                if pending_meeting is not None and pending_meeting.end_tick is None:
                    pending_meeting.end_tick = tick
                pending_meeting = Meeting(
                    call_tick=tick,
                    caller_slot=slot,
                    kind="body" if key == "vote_called_body" else "button",
                )
                meetings.append(pending_meeting)
            elif key == "vote_cast":
                if pending_meeting is not None:
                    target = value.get("target_slot")
                    pending_meeting.votes.append(
                        Vote(tick=tick, voter_slot=slot, target_slot=None if value.get("target") == "skip" else target)
                    )
            elif key == "chat":
                if pending_meeting is not None:
                    pending_meeting.chats.append(ChatLine(tick=tick, slot=slot, text=value.get("text", "")))
            elif key == "phase":
                if value.get("phase") in ("Playing", "GameOver") and pending_meeting is not None and pending_meeting.end_tick is None:
                    pending_meeting.end_tick = tick
            elif key == "completed_task":
                task_completions.append(
                    TaskCompletion(tick=tick, slot=slot, task=value.get("task", -1), while_dead=value.get("while_dead", False))
                )
            elif key == "trace_complete":
                complete = value.get("complete", False)

    for samples in states.values():
        samples.sort(key=lambda s: s.tick)
    for intervals in visibility.values():
        intervals.sort()
    for intervals in body_visibility.values():
        intervals.sort()

    return Game(
        episode=episode or path.name.removesuffix(".jsonl.gz").removesuffix(".jsonl"),
        config=config,
        players=players,
        states=states,
        visibility=visibility,
        body_visibility=body_visibility,
        kills=kills,
        bodies=bodies,
        ejections=ejections,
        meetings=meetings,
        task_completions=task_completions,
        vents=vents,
        task_sites=task_sites,
        tick_count=tick_count,
        complete=complete,
    )
