"""Per-(observer, suspect) suspicion features from a parsed Game.

Stage C of the suspicion-learning pipeline (design §5). One row per
(crew observer, alive suspect, meeting decision point). All evidence is
**cumulative** from tick 0 to the decision tick — never cleared at meetings — and
**visibility-clipped**: a positional cue counts only at sampled ticks where the
observer's rendered-view visibility interval covers the suspect (so offline
features equal what a player in that seat could actually have seen).

Public (meeting) evidence needs no visibility: votes, reports, button calls, and
chat stance triples — from *prior* meetings only, so a row never contains
information from the meeting it decides in (no look-ahead).

Feature values are RAW counts/durations; shaping (binning) happens at fit time.
Every feature here must stay runtime-admissible — computable from crewborg's own
perception + meeting observations (design §5, admissibility rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from replay_parse import Game, Meeting

# --- distance gates (world px; kill_range=20, report_range=20 per game config) ---
NEAR_BODY_DIST = 32          # "right around the body"
FOLLOW_DIST = 48             # shadowing range
TAIL_DIST = 60               # tailing range around the observer
KILL_RANGE_PAD = 8           # co-presence-in-kill-range gate = kill_range + pad
VENT_MARGIN = 4              # standing on a vent
FOLLOW_WINDOW_TICKS = 144    # pre-death window in which following counts (~6 s)

FEATURE_NAMES = [
    # graded positional cues (visibility-clipped, instance-summed)
    "witnessed_kills",
    "near_body_bodies",
    "follow_death_samples",
    "tail_obs_samples",
    "tail_obs_max_run",
    "vent_visits",
    # exculpatory candidates
    "tasks_completed_watched",
    "copresence_killrange_samples",
    "task_site_dwell_samples",
    # exposure (lets the model weigh evidence against opportunity)
    "observed_samples",
    # public / meeting evidence (prior meetings only)
    "reported_bodies",
    "button_calls_made",
    "votes_cast",
    "votes_skipped",
    "voted_against_observer",
    "vote_agreement_with_observer",
    "accusations_made",
    "times_accused",
    "times_defended",
]


@dataclass
class StanceTriple:
    meeting_idx: int
    speaker_slot: int
    stance: str           # "accuses" | "defends"
    target_slot: int


def _color_pattern(game: Game) -> tuple[re.Pattern, dict[str, int]]:
    """Regex alternation over this game's player colors -> slot lookup."""
    by_color = {p.color.lower(): p.slot for p in game.players.values() if p.color}
    # longest first so "pale blue" beats "blue"
    alternation = "|".join(sorted((re.escape(c) for c in by_color), key=len, reverse=True))
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE), by_color


ACCUSE_HINT = re.compile(r"\bsus\b|\bvote\b|\bsaw (?:them|him|her|it)\b", re.IGNORECASE)
DEFEND_HINT = re.compile(r"\bclear(?:ed)?\b|\bsafe\b|\binnocent\b|\bnot sus\b|\bwasn'?t\b", re.IGNORECASE)


def chat_stances(game: Game) -> list[StanceTriple]:
    """Reduce templated meeting chat to (speaker, stance, target) triples (design §10).

    Unparseable lines are dropped, never guessed at. A line naming a color with an
    accuse hint ("<color> sus…", "vote <color>") is an accusation; with a defend
    hint, a defense. Lines about the speaker themselves are ignored.
    """
    pattern, by_color = _color_pattern(game)
    triples: list[StanceTriple] = []
    for mi, meeting in enumerate(game.meetings):
        for chat in meeting.chats:
            text = chat.text or ""
            named = [by_color[m.group(1).lower()] for m in pattern.finditer(text)]
            named = [s for s in named if s != chat.slot]
            if not named:
                continue
            if DEFEND_HINT.search(text):
                stance = "defends"
            elif ACCUSE_HINT.search(text):
                stance = "accuses"
            else:
                continue
            # the first non-self color named is the subject of the sentence
            triples.append(StanceTriple(meeting_idx=mi, speaker_slot=chat.slot, stance=stance, target_slot=named[0]))
    return triples


def _alive_connected_at(game: Game, slot: int, tick: int) -> bool:
    state = game.state_at(slot, tick)
    return bool(state and state.alive and state.connected)


def _max_run(flags: list[bool]) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def extract_rows(game: Game) -> list[dict]:
    """All (observer, suspect, meeting) feature rows for one game."""
    if not game.players or not game.meetings:
        return []
    stances = chat_stances(game)
    rows: list[dict] = []

    for meeting_idx, meeting in enumerate(game.meetings):
        t_decide = meeting.call_tick
        prior_meetings = game.meetings[:meeting_idx]
        for obs in game.players.values():
            if obs.role != "crew" or not _alive_connected_at(game, obs.slot, t_decide):
                continue
            for sus in game.players.values():
                if sus.slot == obs.slot or not _alive_connected_at(game, sus.slot, t_decide):
                    continue
                feats = _pair_features(game, obs.slot, sus.slot, t_decide, prior_meetings, stances, meeting_idx)
                rows.append(
                    {
                        "episode": game.episode,
                        "meeting_idx": meeting_idx,
                        "decision_tick": t_decide,
                        "observer_slot": obs.slot,
                        "observer_name": obs.name,
                        "suspect_slot": sus.slot,
                        "suspect_name": sus.name,
                        "label_imposter": int(game.players[sus.slot].role == "imposter"),
                        **feats,
                    }
                )
    return rows


def _pair_features(
    game: Game,
    obs: int,
    sus: int,
    t: int,
    prior_meetings: list[Meeting],
    stances: list[StanceTriple],
    meeting_idx: int,
) -> dict:
    f = dict.fromkeys(FEATURE_NAMES, 0)

    # --- witnessed kills: kill event while the observer could see the killer ----
    for kill in game.kills:
        if kill.tick < t and kill.killer_slot == sus and game.sees(obs, sus, kill.tick):
            f["witnessed_kills"] += 1

    # --- positional cues over the suspect's sampled states ----------------------
    near_bodies: set[str] = set()
    tail_flags: list[bool] = []
    vent_inside_prev = False
    for s in game.states.get(sus, ()):
        if s.tick >= t:
            break
        if not s.alive:
            tail_flags.append(False)
            continue
        seen = game.sees(obs, sus, s.tick)
        if not seen:
            tail_flags.append(False)
            vent_inside_prev = False
            continue
        f["observed_samples"] += 1

        o = game.state_at(obs, s.tick)
        if o is not None:
            d2 = (s.x - o.x) ** 2 + (s.y - o.y) ** 2
            tailing = d2 <= TAIL_DIST**2
            tail_flags.append(tailing)
            if tailing:
                f["tail_obs_samples"] += 1
            kill_range = int(game.config.get("kill_range", 20)) + KILL_RANGE_PAD
            if d2 <= kill_range**2:
                f["copresence_killrange_samples"] += 1
        else:
            tail_flags.append(False)

        for body in game.bodies:
            if body.spawn_tick <= s.tick and body.victim_slot != sus:
                if (s.x - body.x) ** 2 + (s.y - body.y) ** 2 <= NEAR_BODY_DIST**2 and game.sees_body(
                    obs, body.key, s.tick
                ):
                    near_bodies.add(body.key)

        inside_vent = any(v.contains(s.x, s.y, VENT_MARGIN) for v in game.vents)
        if inside_vent and not vent_inside_prev:
            f["vent_visits"] += 1
        vent_inside_prev = inside_vent

        if any(site.contains(s.x, s.y) for site in game.task_sites):
            f["task_site_dwell_samples"] += 1

    f["near_body_bodies"] = len(near_bodies)
    f["tail_obs_max_run"] = _max_run(tail_flags)

    # --- follow-to-death: observed co-presence with victims shortly before death -
    for kill in game.kills:
        if kill.tick >= t or kill.victim_slot == sus:
            continue
        for s in game.states.get(sus, ()):
            if kill.tick - FOLLOW_WINDOW_TICKS <= s.tick <= kill.tick and game.sees(obs, sus, s.tick):
                v = game.state_at(kill.victim_slot, s.tick)
                if v is not None and (s.x - v.x) ** 2 + (s.y - v.y) ** 2 <= FOLLOW_DIST**2:
                    f["follow_death_samples"] += 1

    # --- exculpatory: watched them complete a real task --------------------------
    for completion in game.task_completions:
        if completion.tick < t and completion.slot == sus and not completion.while_dead:
            if game.sees(obs, sus, completion.tick):
                f["tasks_completed_watched"] += 1

    # --- public meeting evidence (prior meetings only) ----------------------------
    for pm in prior_meetings:
        if pm.kind == "body" and pm.caller_slot == sus:
            f["reported_bodies"] += 1
        if pm.kind == "button" and pm.caller_slot == sus:
            f["button_calls_made"] += 1
        my_vote = next((v.target_slot for v in pm.votes if v.voter_slot == obs), None)
        for vote in pm.votes:
            if vote.voter_slot != sus:
                continue
            if vote.target_slot is None:
                f["votes_skipped"] += 1
            else:
                f["votes_cast"] += 1
                if vote.target_slot == obs:
                    f["voted_against_observer"] += 1
                if my_vote is not None and vote.target_slot == my_vote:
                    f["vote_agreement_with_observer"] += 1
    for triple in stances:
        if triple.meeting_idx >= meeting_idx:
            continue
        if triple.speaker_slot == sus and triple.stance == "accuses":
            f["accusations_made"] += 1
        if triple.target_slot == sus:
            if triple.stance == "accuses":
                f["times_accused"] += 1
            else:
                f["times_defended"] += 1
    return f
