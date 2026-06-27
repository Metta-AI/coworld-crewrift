from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any

from .events import EventRow, common_value


@dataclass(frozen=True)
class AnalysisConfig:
    near_distance: float = 32.0
    body_distance: float = 36.0
    group_distance: float = 44.0
    min_interval_ticks: int = 24

    @classmethod
    def from_env(cls) -> "AnalysisConfig":
        return cls(
            near_distance=float(os.environ.get("CREWRIFT_EVENT_NEAR_DISTANCE", "32")),
            body_distance=float(os.environ.get("CREWRIFT_EVENT_BODY_DISTANCE", "36")),
            group_distance=float(os.environ.get("CREWRIFT_EVENT_GROUP_DISTANCE", "44")),
            min_interval_ticks=int(os.environ.get("CREWRIFT_EVENT_MIN_INTERVAL_TICKS", "24")),
        )


@dataclass(frozen=True)
class PlayerState:
    ts: int
    player: int
    x: float
    y: float
    room: str | None
    phase: str | None
    alive: bool
    connected: bool


@dataclass(frozen=True)
class BodyState:
    ts: int
    victim_slot: int
    x: float
    y: float
    room: str | None
    phase: str | None


@dataclass
class BodyProximity:
    start: int
    last_tick: int
    ticks: list[int]
    distances: list[float]
    rooms: set[str]


def derive_events(rows: list[EventRow], *, episode_id: str, config: AnalysisConfig | None = None) -> list[EventRow]:
    config = config or AnalysisConfig.from_env()
    snapshots = player_snapshots(rows)
    derived: list[EventRow] = []
    derived.extend(proximity_events(snapshots, episode_id=episode_id, config=config))
    derived.extend(route_events(rows, episode_id=episode_id))
    derived.extend(task_attempt_events(rows, episode_id=episode_id, config=config))
    derived.extend(near_body_events(rows, snapshots, episode_id=episode_id, config=config))
    return derived


def player_snapshots(rows: list[EventRow]) -> dict[int, dict[int, PlayerState]]:
    snapshots: dict[int, dict[int, PlayerState]] = defaultdict(dict)
    for row in rows:
        if row.key != "player_state" or row.player < 0:
            continue
        value = row.value
        if not {"x", "y"} <= value.keys():
            continue
        snapshots[row.ts][row.player] = PlayerState(
            ts=row.ts,
            player=row.player,
            x=float(value["x"]),
            y=float(value["y"]),
            room=value.get("room"),
            phase=value.get("phase"),
            alive=bool(value.get("alive", True)),
            connected=bool(value.get("connected", True)),
        )
    return dict(snapshots)


def body_states(rows: list[EventRow]) -> list[BodyState]:
    states: list[BodyState] = []
    for row in rows:
        if row.key != "body_state":
            continue
        value = row.value
        if not {"x", "y", "victim_slot"} <= value.keys():
            continue
        states.append(
            BodyState(
                ts=row.ts,
                victim_slot=int(value["victim_slot"]),
                x=float(value["x"]),
                y=float(value["y"]),
                room=value.get("room"),
                phase=value.get("phase"),
            )
        )
    return states


def distance(a: PlayerState, b: PlayerState | BodyState) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def proximity_events(
    snapshots: dict[int, dict[int, PlayerState]],
    *,
    episode_id: str,
    config: AnalysisConfig,
) -> list[EventRow]:
    players = sorted({player for states in snapshots.values() for player in states})
    rows: list[EventRow] = []
    for index, player_a in enumerate(players):
        for player_b in players[index + 1 :]:
            samples: list[tuple[int, PlayerState, PlayerState, float, bool]] = []
            for ts in sorted(snapshots):
                state_a = snapshots[ts].get(player_a)
                state_b = snapshots[ts].get(player_b)
                if state_a is None or state_b is None or not state_a.alive or not state_b.alive:
                    maybe_emit_pair_interval(
                        samples,
                        rows,
                        episode_id=episode_id,
                        config=config,
                        end_tick=ts,
                        ended_by="player_absent_or_dead",
                    )
                    samples = []
                    continue
                dist = distance(state_a, state_b)
                if dist <= config.near_distance:
                    isolated = pair_is_isolated(snapshots[ts], player_a, player_b, config.group_distance)
                    samples.append((ts, state_a, state_b, dist, isolated))
                else:
                    maybe_emit_pair_interval(
                        samples,
                        rows,
                        episode_id=episode_id,
                        config=config,
                        end_tick=ts,
                        ended_by="separation",
                    )
                    samples = []
            if samples:
                maybe_emit_pair_interval(
                    samples,
                    rows,
                    episode_id=episode_id,
                    config=config,
                    end_tick=samples[-1][0],
                    ended_by="trace_end",
                )
    return rows


def pair_is_isolated(states: dict[int, PlayerState], player_a: int, player_b: int, threshold: float) -> bool:
    a = states[player_a]
    b = states[player_b]
    for player, state in states.items():
        if player in {player_a, player_b} or not state.alive:
            continue
        if distance(a, state) <= threshold or distance(b, state) <= threshold:
            return False
    return True


def maybe_emit_pair_interval(
    samples: list[tuple[int, PlayerState, PlayerState, float, bool]],
    rows: list[EventRow],
    *,
    episode_id: str,
    config: AnalysisConfig,
    end_tick: int,
    ended_by: str,
) -> None:
    if len(samples) < 2:
        return
    start = samples[0][0]
    duration = end_tick - start
    if duration < config.min_interval_ticks:
        return

    player_a = samples[0][1].player
    player_b = samples[0][2].player
    distances = [sample[3] for sample in samples]
    sample_ticks = [sample[0] for sample in samples]
    rooms = sorted({state.room for sample in samples for state in sample[1:3] if state.room})
    isolated_fraction = sum(1 for sample in samples if sample[4]) / len(samples)
    value = common_value(
        source="derived",
        confidence=0.82,
        episode_id=episode_id,
        phase=most_common([sample[1].phase for sample in samples]),
        tick_start=start,
        tick_end=end_tick,
        last_observed_tick=samples[-1][0],
        duration_ticks=duration,
        boundary_precision=interval_boundary_precision(sample_ticks, end_tick),
        ended_by=ended_by,
        player_a=player_a,
        player_b=player_b,
        min_distance=round(min(distances), 3),
        median_distance=round(median(distances), 3),
        max_distance=round(max(distances), 3),
        rooms=rooms,
        sample_count=len(samples),
        evidence=[
            f"distance <= {config.near_distance:g} for {duration} ticks",
            f"{len(samples)} sampled co-location points",
        ],
    )
    rows.append(EventRow(ts=start, player=-1, key="proximity_interval", value=value))
    if isolated_fraction >= 0.8:
        rows.append(
            EventRow(
                ts=start,
                player=-1,
                key="isolation_interval",
                value={
                    **value,
                    "confidence": 0.76,
                    "isolated_fraction": round(isolated_fraction, 3),
                    "evidence": value["evidence"] + ["no third living player stayed near either player"],
                },
            )
        )
    rows.extend(
        following_or_chase_events(
            samples,
            episode_id=episode_id,
            end_tick=end_tick,
            boundary_precision=value["boundary_precision"],
            ended_by=ended_by,
        )
    )


def following_or_chase_events(
    samples: list[tuple[int, PlayerState, PlayerState, float, bool]],
    *,
    episode_id: str,
    end_tick: int,
    boundary_precision: str,
    ended_by: str,
) -> list[EventRow]:
    rows: list[EventRow] = []
    for follower_index, target_index in ((1, 2), (2, 1)):
        score = movement_alignment(samples, follower_index=follower_index, target_index=target_index)
        if score["moving_samples"] < 3:
            continue
        if score["alignment_ratio"] >= 0.6 and score["lag_ratio"] >= 0.35:
            follower = samples[0][follower_index].player
            target = samples[0][target_index].player
            confidence = min(0.88, 0.42 + score["alignment_ratio"] * 0.28 + score["lag_ratio"] * 0.18)
            rows.append(
                EventRow(
                    ts=samples[0][0],
                    player=follower,
                    key="following_interval",
                    value=common_value(
                        source="derived",
                        confidence=round(confidence, 3),
                        episode_id=episode_id,
                        phase=most_common([sample[follower_index].phase for sample in samples]),
                        tick_start=samples[0][0],
                        tick_end=end_tick,
                        last_observed_tick=samples[-1][0],
                        duration_ticks=end_tick - samples[0][0],
                        boundary_precision=boundary_precision,
                        ended_by=ended_by,
                        follower=follower,
                        target=target,
                        alignment_ratio=score["alignment_ratio"],
                        lag_ratio=score["lag_ratio"],
                        evidence=[
                            "movement vectors aligned over the interval",
                            "follower repeatedly occupied recent target positions",
                        ],
                    ),
                )
            )
            if samples[0][3] - samples[-1][3] >= 8:
                rows.append(
                    EventRow(
                        ts=samples[0][0],
                        player=follower,
                        key="chase_interval",
                        value=common_value(
                            source="derived",
                            confidence=round(min(0.9, confidence + 0.05), 3),
                            episode_id=episode_id,
                            phase=most_common([sample[follower_index].phase for sample in samples]),
                            tick_start=samples[0][0],
                            tick_end=end_tick,
                            last_observed_tick=samples[-1][0],
                            duration_ticks=end_tick - samples[0][0],
                            boundary_precision=boundary_precision,
                            ended_by=ended_by,
                            chaser=follower,
                            target=target,
                            start_distance=round(samples[0][3], 3),
                            end_distance=round(samples[-1][3], 3),
                            evidence=["distance decreased during a following interval"],
                        ),
                    )
                )
    return rows


def interval_boundary_precision(ticks: list[int], end_tick: int) -> str:
    if not ticks:
        return "sampled"
    consecutive_samples = all(current - previous == 1 for previous, current in zip(ticks, ticks[1:]))
    end_is_measured = end_tick == ticks[-1] or end_tick - ticks[-1] == 1
    if consecutive_samples and end_is_measured:
        return "exact"
    return "sampled"


def movement_alignment(
    samples: list[tuple[int, PlayerState, PlayerState, float, bool]],
    *,
    follower_index: int,
    target_index: int,
) -> dict[str, float | int]:
    aligned = 0
    lagged = 0
    moving = 0
    for previous, current in zip(samples, samples[1:]):
        follower_prev = previous[follower_index]
        follower_cur = current[follower_index]
        target_prev = previous[target_index]
        target_cur = current[target_index]
        follower_dx = follower_cur.x - follower_prev.x
        follower_dy = follower_cur.y - follower_prev.y
        target_dx = target_cur.x - target_prev.x
        target_dy = target_cur.y - target_prev.y
        follower_mag = math.hypot(follower_dx, follower_dy)
        target_mag = math.hypot(target_dx, target_dy)
        if follower_mag < 1 or target_mag < 1:
            continue
        moving += 1
        if follower_dx * target_dx + follower_dy * target_dy > 0:
            aligned += 1
        if math.hypot(follower_cur.x - target_prev.x, follower_cur.y - target_prev.y) <= previous[3] + 4:
            lagged += 1
    if moving == 0:
        return {"alignment_ratio": 0.0, "lag_ratio": 0.0, "moving_samples": 0}
    return {
        "alignment_ratio": round(aligned / moving, 3),
        "lag_ratio": round(lagged / moving, 3),
        "moving_samples": moving,
    }


def route_events(rows: list[EventRow], *, episode_id: str) -> list[EventRow]:
    by_player: dict[int, list[EventRow]] = defaultdict(list)
    for row in rows:
        if row.key in {"left_room", "entered_room"} and row.player >= 0:
            by_player[row.player].append(row)

    derived: list[EventRow] = []
    for player, events in by_player.items():
        pending_left: EventRow | None = None
        for row in sorted(events, key=lambda item: item.ts):
            if row.key == "left_room":
                pending_left = row
            elif row.key == "entered_room" and pending_left is not None:
                target_room = row.value.get("room")
                derived.append(
                    EventRow(
                        ts=pending_left.ts,
                        player=player,
                        key="headed_to",
                        value=common_value(
                            source="derived",
                            confidence=0.72,
                            episode_id=episode_id,
                            phase=row.value.get("phase"),
                            target_kind="room",
                            target_name=target_room,
                            tick_start=pending_left.ts,
                            tick_end=row.ts,
                            origin_room=pending_left.value.get("room"),
                            evidence=["player left one room and next entered target room"],
                        ),
                    )
                )
                derived.append(
                    EventRow(
                        ts=row.ts,
                        player=player,
                        key="arrived_at",
                        value=common_value(
                            source="derived",
                            confidence=1.0,
                            episode_id=episode_id,
                            phase=row.value.get("phase"),
                            target_kind="room",
                            target_name=target_room,
                            tick_start=pending_left.ts,
                            tick_end=row.ts,
                            evidence=["direct entered_room replay event"],
                        ),
                    )
                )
                pending_left = None
    return derived


def task_attempt_events(rows: list[EventRow], *, episode_id: str, config: AnalysisConfig) -> list[EventRow]:
    by_player: dict[int, list[EventRow]] = defaultdict(list)
    for row in rows:
        if row.key in {"started_task", "completed_task", "died", "phase"}:
            by_player[row.player].append(row)

    derived: list[EventRow] = []
    for player, events in by_player.items():
        if player < 0:
            continue
        active: EventRow | None = None
        for row in sorted(events, key=lambda item: item.ts):
            if row.key == "started_task":
                if active is not None and row.ts - active.ts >= config.min_interval_ticks:
                    derived.append(task_attempt_row(active, row.ts, episode_id, "abandoned", "another task started"))
                active = row
            elif row.key == "completed_task" and active is not None and row.value.get("task") == active.value.get("task"):
                derived.append(task_attempt_row(active, row.ts, episode_id, "completed", "completed_task event"))
                active = None
            elif row.key in {"died", "phase"} and active is not None and row.ts - active.ts >= config.min_interval_ticks:
                reason = "player died" if row.key == "died" else f"phase changed to {row.value.get('phase')}"
                derived.append(task_attempt_row(active, row.ts, episode_id, "abandoned", reason))
                active = None
    return derived


def task_attempt_row(start: EventRow, end_tick: int, episode_id: str, outcome: str, reason: str) -> EventRow:
    return EventRow(
        ts=start.ts,
        player=start.player,
        key="task_attempt",
        value=common_value(
            source="derived",
            confidence=0.9 if outcome == "completed" else 0.68,
            episode_id=episode_id,
            phase=start.value.get("phase"),
            task=start.value.get("task"),
            outcome=outcome,
            reason=reason,
            tick_start=start.ts,
            tick_end=end_tick,
            duration_ticks=end_tick - start.ts,
        ),
    )


def near_body_events(
    rows: list[EventRow],
    snapshots: dict[int, dict[int, PlayerState]],
    *,
    episode_id: str,
    config: AnalysisConfig,
) -> list[EventRow]:
    bodies_by_tick: dict[int, list[BodyState]] = defaultdict(list)
    for body in body_states(rows):
        bodies_by_tick[body.ts].append(body)

    active: dict[tuple[int, int], BodyProximity] = {}
    emitted: list[EventRow] = []
    ticks = sorted(set(snapshots) | set(bodies_by_tick))
    for ts in ticks:
        states = snapshots.get(ts, {})
        bodies = bodies_by_tick.get(ts, [])
        present: set[tuple[int, int]] = set()
        for body in bodies:
            for player, state in states.items():
                if player == body.victim_slot or not state.alive:
                    continue
                dist = distance(state, body)
                if dist <= config.body_distance:
                    key = (player, body.victim_slot)
                    present.add(key)
                    if key not in active:
                        active[key] = BodyProximity(start=ts, last_tick=ts, ticks=[], distances=[], rooms=set())
                    proximity = active[key]
                    proximity.last_tick = ts
                    proximity.ticks.append(ts)
                    proximity.distances.append(dist)
                    if body.room:
                        proximity.rooms.add(body.room)
        for key in list(active):
            if key not in present:
                proximity = active.pop(key)
                append_near_body_event(emitted, key, proximity, ts, "body_or_player_left_range", episode_id, config)
    for key, proximity in active.items():
        append_near_body_event(emitted, key, proximity, proximity.last_tick, "trace_end", episode_id, config)
    return emitted


def append_near_body_event(
    emitted: list[EventRow],
    key: tuple[int, int],
    proximity: BodyProximity,
    end_tick: int,
    ended_by: str,
    episode_id: str,
    config: AnalysisConfig,
) -> None:
    duration = end_tick - proximity.start
    if duration < config.min_interval_ticks or not proximity.distances:
        return
    emitted.append(
        EventRow(
            ts=proximity.start,
            player=key[0],
            key="near_body_interval",
            value=common_value(
                source="derived",
                confidence=0.84,
                episode_id=episode_id,
                victim_slot=key[1],
                tick_start=proximity.start,
                tick_end=end_tick,
                last_observed_tick=proximity.last_tick,
                duration_ticks=duration,
                boundary_precision=interval_boundary_precision(proximity.ticks, end_tick),
                ended_by=ended_by,
                min_distance=round(min(proximity.distances), 3),
                median_distance=round(median(proximity.distances), 3),
                rooms=sorted(proximity.rooms),
                evidence=[f"player remained within {config.body_distance:g} px of body"],
            ),
        )
    )


def most_common(values: list[Any]) -> Any:
    counts: dict[Any, int] = defaultdict(int)
    for value in values:
        if value is not None:
            counts[value] += 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]
