---
name: coworld-episode-artifacts
description: "Use to identify and download Coworld episode artifacts — replays, results, and per-agent logs — for completed episodes. Triggers: 'download the replay/logs for episode X', 'pull the last N episodes <player> played', 'fetch artifacts for this experience request / pool / round', 'get the episode data for post-mortem'. Game-agnostic (Crewrift, amongthem, etc.)."
---

# Coworld Episode Artifacts

Find completed Coworld episodes and download everything the Observatory data API
will hand back for each — into one self-contained directory per episode, ready to
replay, diff, or post-mortem offline. Game-agnostic: works for any Coworld game
(Crewrift, amongthem, …), not one specific player.

**Announce at start:** "Downloading Coworld episode artifacts. I'll resolve the
episodes for your selection, then pull replay + results + per-agent logs per
episode."

## What you get, per episode

One directory per episode (`<timestamp>_<short-id>/`) containing:

- `episode.json` — the source record (league episode detail, or the
  experience-request episode row: participants, scores, status, game_config).
- `results.json` — scores / metrics / win / per-agent outcomes.
- `replay.json` + `replay.json.z` — the game replay, decompressed (the
  directly-loadable binary form) plus the raw zlib blob.
- `logs/policy_agent_{N}.log` — each agent's full per-tick stderr trace (the
  richest behavioural record).
- `artifacts/policy_artifact_{N}.zip` — any player-uploaded telemetry/debug
  bundles (policy-scoped: only slots you own come back; e.g. crewborg's trace
  zip with `telemetry.jsonl` + `manifest.json`). **This is where you verify
  crewborg's LLM/Bedrock** — `telemetry.jsonl` carries `domain.meeting_llm_decision`
  vs `_fallback` (see [Bedrock debugging](../../docs/reference/coworld-platform.md#bedrock--in-pod-llm))
  — and it is **not** subject to the hosted ~10k-line log cap, so prefer it over `logs/`.
- `error_info.json` — only when the episode failed.

Plus a top-level `index.json` summarizing the run. Every artifact is best-effort:
a missing replay or one missing log is logged and recorded, never aborts the run.

## The model (read this before debugging a 404)

Everything keys off **`job_id`**, the universal artifact handle every episode
carries. Artifacts come from `/jobs/{job_id}/artifacts/{results,replay,error_info}`
and `/jobs/{job_id}/policy-logs[/{idx}]`. There are two disjoint episode
populations — **league/tournament** episodes (discovered by policy) and
**experience-request** episodes (discovered by request/pool/round) — but both
expose the same `job_id`, so one code path serves both. Full route map, dead-ends,
and the recurring client/server drift: **`references/endpoint-map.md`**. Read it
whenever a route 4xxs; the published `coworld` client regularly ships behind the
server.

## Workflow

1. **Resolve live IDs first — never reuse cached ones.** League/division/round/
   pool/policy-version IDs rotate. For a policy by name the script resolves
   versions for you. For a pool/round/division/experience-request, get the current
   ID from the relevant CLI (`coworld pools|rounds|divisions|xp-request list`) or
   the Observatory UI and pass it in.

2. **Pick exactly one discovery mode** and run the downloader. Auth comes from
   `softmax login`; run inside `uv run` from an environment with the Coworld SDK
   (`coworld[auth]`) installed so `softmax` is importable.

   ```bash
   F=players/crewborg/skills/coworld-episode-artifacts/scripts/fetch_artifacts.py

   # A policy's most recent league episodes (across all its versions):
   uv run python "$F" --policy crewborg -n 10 --out /tmp/crewborg_eps
   # All child episodes of one experience request:
   uv run python "$F" --xreq xreq_... --out /tmp/xreq_eps
   # Explicit experience-request episodes (repeatable):
   uv run python "$F" --ereq ereq_aaa... --ereq ereq_bbb... --out /tmp/eps
   # Everything in a pool / round / division:
   uv run python "$F" --pool pool_... -n 50 --out /tmp/pool_eps
   # Explicit league episode uuids:
   uv run python "$F" --episode <uuid> --out /tmp/eps
   ```

   Useful flags: `-n/--num` (cap for policy/xreq/pool/round/division modes),
   `--version N` (with `--policy`), `--no-replay` / `--no-results` / `--no-logs`
   (skip a category), `--force` (re-download complete dirs), `--server` (override
   the API base). Runs are **idempotent** — complete episode dirs are skipped.

3. **Use the artifacts.** *How* to read each — the viewer vs the version-matched
   `expand_replay` vs the policy logs, the `.bitreplay` format, the slot↔policy
   mapping, and the hosted-log cap — is in
   [`crewrift-replays.md`](../../docs/reference/crewrift-replays.md). In short:
   `results.json` + the episode row drive scoring; `logs/` and the
   `artifacts/*.zip` telemetry hold per-agent behaviour; replays open in the viewer
   (`uv run coworld replay <coworld_id> <replay.json>`) or `expand_replay`. Turn a
   whole batch into a report with the **`crewrift-report`** skill.

## Notes

- For *interactive* one-off inspection of a single experience-request episode, the
  `coworld` CLI (`coworld episodes|replays|episode-logs|episode-results`) is
  fine — see `references/endpoint-map.md`. This script is for discovering across a
  policy's versions and bundling everything per episode in one pass, and it reads
  raw routes so it survives client/server skew.
- For *creating* the experience requests whose episodes this skill then downloads,
  use the `coworld-experience-requests` skill.
- If auth fails with an `AttributeError` on `load_current_cogames_token`, you're
  looking at an older tool — the current API is `load_current_token(server=...)`.
