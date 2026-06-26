---
name: coworld-operations
description: Coworld operations guide for optimizer agents. Use when reading leaderboards, memberships, submissions, creating or polling XP/experience requests, downloading replays/artifacts, uploading policy images, submitting policies to leagues, or checking active player/auth state.
---

# Coworld Operations

Use this guide for platform mechanics. Game strategy belongs in game-specific
skills.

## Principles

- Treat policy identity carefully: policy name/version refs and policy version
  UUIDs are different identifiers.
- Verify the active player before uploads/submissions.
- Save every request body and response; replay/artifact data is part of the
  optimization evidence.
- Prefer JSON output when available.
- For hosted evals, use the smallest request size that gives signal; split large
  batches if the backend is unstable at the max boundary.

## Auth And Context

Check CLI/auth/Docker status before mutating work:

```bash
uv run coworld player list --json
uv run coworld player create "<optimizer_player_name>"
uv run coworld player use <player_id>
uv run coworld leagues --json
uv run coworld memberships --mine --json
```

## Leaderboard And League Reads

List or inspect leagues:

```bash
uv run coworld leagues --json
uv run coworld leagues <league_id> --json
```

Read active league memberships:

```bash
uv run coworld memberships --league <league_id> --active-only --limit 1000 --json
```

Read your own memberships/submissions:

```bash
uv run coworld memberships --mine --json
uv run coworld submissions --mine --limit 100 --json
```

Read policy-specific or player-specific records:

```bash
uv run coworld memberships --league <league_id> --policy <policy_ref_or_version_id> --json
uv run coworld submissions --league <league_id> --player <player_id> --json
```

## Creating XP / Experience Requests

Hosted XP requests evaluate submitted policies on the hosted backend. They are
the right tool for league-relevant policy-vs-policy tests.

Pairwise request body:

```json
{
  "target": { "league_id": "<league_id>" },
  "roster": [
    { "player": { "policy_ref": "<our_policy_version_id>" }, "slot": -1 },
    { "player": { "policy_ref": "<opponent_policy_version_id>" }, "slot": -1 }
  ],
  "num_episodes": 40,
  "notes": "optimizer pairwise: our_policy vs opponent_policy"
}
```

Top-N request body:

```json
{
  "target": { "league_id": "<league_id>" },
  "roster": [
    { "player": { "policy_ref": "<our_policy_version_id>" }, "slot": -1 },
    { "player": { "top_n": 3 }, "slot": -1 }
  ],
  "num_episodes": 40,
  "notes": "optimizer broad guardrail: our_policy vs top_n=3"
}
```

Create the request:

```bash
uv run coworld xp-request create request.json --json
```

Poll request and episodes:

```bash
uv run coworld xp-request get <xreq_id> --json
uv run coworld xp-request episodes <xreq_id> --json
uv run coworld xp-request list --mine --json
```

## Downloading Replays And Artifacts

For each completed or failed episode request, save:

- episode row,
- replay URL payload,
- our artifact zip/database,
- our hosted stdout/stderr logs or a saved tail/summary,
- log triage summary with tracebacks, malformed actions, provider failures,
  timeout/crash symptoms, and whether artifacts were emitted.

Artifacts are the durable learning dataset. Hosted stdout/stderr logs are still
mandatory because crashes can happen before artifacts are flushed. Always inspect
the optimizer player's own log tab in Softmax Observatory, and enable "other
player logs" only when opponent logs are needed to explain an episode.

CLI artifact download:

```bash
uv run coworld episode-logs <episode_request_id> --artifact --mine --download-dir <dir>
```

CLI log inspection:

```bash
uv run coworld episode-logs <episode_request_id> --mine
```

If analyzing another slot explicitly:

```bash
uv run coworld episode-logs <episode_request_id> --agent <slot> --artifact --download-dir <dir>
uv run coworld episode-logs <episode_request_id> --agent <slot>
```

Replay URLs may be compressed. The runtime should download and decompress them
when needed, then store replay JSON beside `episodes.json`.

Minimum log triage fields:

```text
episode_request_id:
policy_ref:
policy_version_id:
player_id:
agent_slot:
stdout_summary:
stderr_summary:
traceback:
exception_type:
source_file_line:
malformed_action:
provider_or_timeout_error:
artifact_present:
verdict: clean | policy_bug | platform_error | needs_data
```

## Building And Uploading Policies

Builds vary by Coworld package. Use the local game repo’s known build command.
Then upload the resulting Docker image as a policy:

```bash
coworld upload-policy <local_image_ref> --name <policy_name> --run python --run /app/player.py
```

Common options:

```bash
coworld upload-policy <image> \
  --name <policy_name> \
  --run python --run /app/v2/coworld/players/my_player.py \
  --use-bedrock \
  --bedrock-model <model_id> \
  --secret-env <ENV_NAME>
```

### Critical: The `--run` Flag

The `--run` flag sets the `run` attribute on the policy version. Without it, the
hosted runtime **cannot start the container** and the policy gets -100 (inactive
timeout penalty). Each argv token needs a separate `--run` flag:

```bash
# CORRECT: two --run flags for "python /app/player.py"
coworld upload-policy img:v1 --name my-policy --run python --run /app/player.py

# WRONG: single --run with spaces (CLI rejects it)
coworld upload-policy img:v1 --name my-policy --run "python /app/player.py"
```

After upload, verify the `run` attribute exists on the new policy version:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://softmax.com/api/observatory/stats/policy-versions/<pvid>" | jq .attributes
```

Expected: `{"run": ["python", "/app/player.py"], "kind": "docker-img"}`

If `run` is missing, re-upload with `--run`. The version number auto-increments.

### Other Upload Flags

- **`--use-bedrock`**: Sets `USE_BEDROCK=true` in the player pod. Required for
  any policy calling AWS Bedrock (runs under tournament IRSA role).
- **`--bedrock-model <model_id>`**: Sets `BEDROCK_MODEL` env var in the pod.
- **`--secret-env KEY=VALUE`**: Stored in AWS Secrets Manager, injected at runtime.

## Secrets / Provider Keys (AWS Secrets Manager)

LLM/provider API keys and any other runtime secret are attached to a **policy
version** at upload time and resolved at runtime from **AWS Secrets Manager** —
they are **never** baked into the Docker image, committed to source, or passed in
the manifest. This is the only supported way to give a hosted policy a secret.

### Upload a secret

Use one `--secret-env KEY=VALUE` per secret on `coworld upload-policy`. The value
is written to Secrets Manager (scoped to that policy version) and injected as the
environment variable `KEY` into every pod that runs the policy version:

```bash
# OpenRouter (the policy's preferred LLM gateway): the value is stored in
# Secrets Manager and injected as OPENROUTER_API_KEY at runtime.
coworld upload-policy <image> \
  --name <policy_name> \
  --run python --run -m --run players.crewrift.crewborg.coworld.policy_player \
  --secret-env OPENROUTER_API_KEY=sk-or-...

# Multiple secrets / non-secret config: repeat the flag.
coworld upload-policy <image> --name <policy_name> \
  --secret-env OPENROUTER_API_KEY=sk-or-... \
  --secret-env CREWBORG_LLM_MODEL=anthropic/claude-haiku-4.5
```

Provider-key conventions for this repo's crewborg-style policies:

| Backend | Secret to attach | How it's selected at runtime |
|---|---|---|
| OpenRouter (preferred) | `--secret-env OPENROUTER_API_KEY=sk-or-...` | A present `OPENROUTER_API_KEY` auto-selects the OpenRouter provider and enables LLM meetings. Force it with `--secret-env CREWBORG_LLM_PROVIDER=openrouter`. |
| Direct Anthropic | `--secret-env ANTHROPIC_API_KEY=sk-ant-...` | Selected when a key is present and no Bedrock flag is set. |
| AWS Bedrock | `--use-bedrock` (= `--secret-env USE_BEDROCK=true`) | Routes the pod to a Bedrock-enabled IRSA service account; AWS creds come from the pod role, not a key. |

### Access a secret at runtime

The injected secret is just an environment variable inside the pod. Read it the
normal way (`os.environ["OPENROUTER_API_KEY"]`). In this codebase, secrets are
resolved **once at the strategy boundary** (`read_meeting_params_from_env` in
`players/crewrift/crewborg/strategy/meeting/llm.py`), carried on `MeetingParams`,
and used to build the LLM client (`build_meeting_client`) — modes never read the
environment themselves.

### Rules

- **Never** hardcode auth tokens or provider keys into policy source, the
  Dockerfile `ENV`, or the manifest. Use `--secret-env` only.
- A secret is scoped to the **policy version** it was uploaded with; a re-upload
  (new version) must re-attach the secrets it needs.
- Keep secret-bearing env vars out of traces/logs. The meeting client stores the
  key on config but never emits it; only model/usage/latency are traced.
- Verify the uploaded policy belongs to the intended active player before relying
  on its secrets.

## Submitting To A League

Submit a ready uploaded policy:

```bash
uv run coworld submit <policy_name:vN> --league <league_id> --auto-champion always --no-open-browser
```

Before submitting:

- active player is correct,
- policy image upload succeeded,
- local syntax/build checks passed,
- hosted guardrail eval passed,
- rollback policy ref is known.

After submitting:

```bash
uv run coworld submissions --league <league_id> --policy <policy_name:vN> --json
uv run coworld memberships --league <league_id> --policy <policy_name:vN> --json
```

Confirm player id, policy version id, status, and champion/qualification state.

## Eval Data To Persist

For every XP/eval:

```text
request-body.json
xp_request.json
episodes.json
replays/<episode_id>.json
artifacts/<episode_id>-<agent>.zip
logs/<episode_request_id>-<agent>.stdout.txt
logs/<episode_request_id>-<agent>.stderr.txt
summary.json
hypotheses.json
verdict.md
```

The optimizer should be able to reconstruct why a policy was changed or promoted
from these files alone.

## Common Failure Modes

- Wrong active player before upload or submit.
- Using policy name ref where the API expects policy version UUID.
- XP request too large or at an unstable backend boundary.
- Request created but immediate read-back briefly 404s; retry by id.
- Artifacts missing because logs were fetched without `--artifact`.
- Policy bug hidden in stdout/stderr, such as a traceback from malformed action
  handling, while aggregate XP status only shows a failed or zero-score episode.
- Promotion based on one pairwise win while broad leaderboard score regresses.

## MCP / Tool Runtime Mapping

If the runtime exposes optimizer MCP tools, map operations as:

- status/auth: `coworld_status`
- standings: `coworld_league_standings`
- XP create: `experience_request_create`
- XP get: `experience_request_get`
- XP episodes: `experience_request_episodes`
- submit: `policy_submit_to_league`
- safety: `guardrail_check`, `budget_check`
- promotion: `eval_status` gate result, then `policy_promote`

Use the runtime’s tool schema as the source of truth for exact argument names.
