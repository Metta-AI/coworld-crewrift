# Experience-Request Benchmark Analysis

Use this workflow when you want a coding agent to benchmark one Coworld policy
against current tournament opponents by creating an Observatory/Coworld
experience request, then summarize how the policy performed.

## Inputs

- Target policy label/version, for example `crewborg:v5`.
- Target league/division, for example `Crewrift Daily` / `Wood`.
- Number of games, for example `15`.
- Authenticated `coworld` CLI / `CoworldApiClient` access.

## Workflow

### 1. Resolve Live Tournament State

Pull the current league, division, leaderboard, memberships, and Coworld rows:

```sh
coworld leagues --json
coworld divisions --league <league_id> --json
coworld results <division_id> --json
coworld memberships --division <division_id> --active-only --json
coworld list --json
```

Verify:

- The league is the current intended league.
- The division ID matches the requested division.
- The Coworld is canonical/current, not a stale older version.
- The target policy version ID is the intended version.

### 2. Join Leaderboard Rows To Active Memberships

Use `player_id` from `coworld results` and match it to active membership rows.
Pull each opponent's active `policy_version.id`.

If a player has multiple active memberships:

- Prefer `substatus == "champion"` when present.
- Otherwise use the newest active membership by `created_at`.
- Record this assumption in the final report.

### 3. Inspect The Current API Schema

The experience-request API can drift. Check OpenAPI before posting:

```python
from coworld.api_client import CoworldApiClient
import json

client = CoworldApiClient.from_login(server_url="https://softmax.com/api")
openapi = json.loads(client.get_text("/openapi.json"))
print(openapi["components"]["schemas"]["V2CreateExperienceRequestRequest"])
```

Important ownership rule:

- `policy_version_ids` is for caller-owned explicit rosters.
- For non-owned tournament opponents, use `requester` plus `opponents`.

### 4. Create The Experience Request

Example request body:

```python
payload = {
    "target": {"division_id": "<division_id>"},
    "requester": {
        "policy_version_id": "<target_policy_version_id>",
        "slot": 0,
    },
    "opponents": [
        {"policy_version_id": "<opponent_1_policy_version_id>"},
        {"policy_version_id": "<opponent_2_policy_version_id>"},
    ],
    "num_episodes": 15,
    "notes": "Target policy vs live division top N; requester slot 0.",
    "backfill": {"enabled": False},
    "execution_backend": "k8s",
}

created = client._post("/v2/experience-requests", dict, json=payload)
```

Immediately read it back:

```python
request_id = created["id"]
detail = client._get(f"/v2/experience-requests/{request_id}", dict)
```

Verify:

- `episode_count` equals the requested count.
- Child episode rows exist.
- Participant order is correct.
- Slot `0` is the requester policy.
- Opponents resolve to the expected policy names/versions.

### 5. Poll For Progress

```python
detail = client._get(f"/v2/experience-requests/{request_id}", dict)
print(detail["status"], detail["completed_count"], detail["failed_count"])
```

Separate these states in the report:

- Request accepted.
- Jobs submitted.
- Games running.
- Games completed.
- Failures.

Do not report "done" just because the request was created.

### 6. Analyze Completed Games

For each completed child episode:

- Find the requester policy score from `scores`.
- Compare the requester score against the episode max score.
- Track ties for top score.
- Capture outlier episode IDs and replay URLs.

Useful summary fields:

- Completed count / requested count.
- Failed count.
- Mean, median, min, and max requester score.
- Number of top-score ties.
- Per-policy average scores.
- Replay URL for bad outliers.

### 7. Report Clearly

Include:

- Request ID.
- Coworld ID/version.
- Status counts.
- Roster with policy UUIDs.
- Target policy performance.
- Outlier episode IDs and replay URLs.
- Any caveats, especially still-running games or ambiguous membership selection.

Example summary:

```text
Request xreq_... is still running: 14/15 completed, 0 failed.

target:vN averaged 100.86 over completed games, median 108, min/max 8/108.
It tied for top score in 13/14 completed games. One bad outlier was ereq_...,
replay: https://...
```
