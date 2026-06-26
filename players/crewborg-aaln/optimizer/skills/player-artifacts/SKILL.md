---
name: player-artifacts
description: Guide for utilizing player artifacts in Coworld policies and optimization loops. Use when adding artifact emission to a policy, downloading artifacts from evals, joining artifact data with replays, or building analysis datasets from episode evidence.
---

# Player Artifacts

Player artifacts are the structured data a policy uploads at episode end for
post-hoc analysis and optimization. They are separate from player logs
(stdout/stderr) and are the preferred data source for learning because logs are
unstructured and may be truncated.

## Contract

The runner injects one environment variable into the player container:

- `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` — presigned PUT URL for this player slot.
  - Hosted: `https://` presigned URL (no auth header needed).
  - Local: `file://` path into the mounted workspace.
  - Absent: skip uploading. Always treat as optional.

Rules:

- Upload exactly one `.zip` per player slot.
- Maximum size: 200 MB.
- HTTP `PUT` with `Content-Type: application/zip`. No auth header (presigned).
- For `file://` URLs, write bytes to that path (create parent dirs).
- Upload before container teardown. The platform does not wait for uploads.
- A missing artifact never fails an otherwise successful episode.
- Never crash the policy on upload failure — catch and log.

## Building The Zip

Bundle whatever is useful: parquet, sqlite, csv, json, or trace files. Two
approaches:

- **Sampling profiler**: dump all per-step state. Useful early, becomes noise.
- **Tracing profiler**: record only specific named events. Better for
  optimization once you know what to look for.

## Recommended Artifact Schema For Optimization

```text
README.md                   # schema version, field definitions, join keys
metadata.json               # episode_id, policy_version_id, seat, game config
decisions.jsonl             # per-decision records
observations.jsonl          # what the policy saw each step
features.jsonl              # parsed features from observations
errors.jsonl                # any errors/fallbacks that occurred
```

### decisions.jsonl fields

```text
episode_id
policy_version_id
seat
round / tick / phase
observation_hash
parsed_features_json
candidate_actions_json
chosen_action
reason_code              # "scripted", "fallback", "llm", "classifier"
confidence
llm_used                 # boolean
llm_model
llm_failed               # boolean
fallback_used            # boolean
error
latency_ms
created_at
```

### metadata.json fields

```text
episode_id
policy_version_id
policy_name
policy_version
seat / slot
opponent_policy_version_id
game_config_snapshot
artifact_schema_version
total_decisions
total_llm_calls
total_fallbacks
total_errors
```

## Python Upload Helper

```python
import io
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


def upload_player_artifact(files: dict[str, bytes]) -> None:
    """Zip files (name -> bytes) and upload to the per-slot artifact URL.

    Safe to call unconditionally: missing env var or failed upload is ignored.
    """
    url = os.environ.get("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL")
    if not url:
        return

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    payload = buffer.getvalue()

    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return

    # Presigned PUT — no auth header needed.
    req = Request(url, data=payload, method="PUT",
                  headers={"Content-Type": "application/zip"})
    try:
        with urlopen(req, timeout=60):
            pass
    except Exception:
        pass  # Missing artifact never fails the episode.
```

Call at episode end:

```python
# After final game message / reveal phase:
upload_player_artifact({
    "metadata.json": json.dumps(metadata).encode(),
    "decisions.jsonl": decisions_buffer.encode(),
    "errors.jsonl": errors_buffer.encode(),
})
```

## Downloading Artifacts From Evals

After an XP eval completes, download artifacts for analysis:

```bash
# Download artifact for our player slot
coworld episode-logs <episode_request_id> --artifact --mine --download-dir <dir>

# Download for a specific agent slot
coworld episode-logs <episode_request_id> --agent 0 --artifact --download-dir <dir>
```

Ownership-scoped API route:

```
GET /v2/episode-requests/{ereq_id}/{policy_version_id}/policy-artifact/{agent_idx}
```

Returns the `.zip` for one owned policy version at one agent slot.

## Using Artifacts In Optimization Loops

### 1. Collect

After each eval set completes:
- Download all artifacts for our player slot.
- Unzip into `artifacts/<episode_id>/` directories.
- Parse `decisions.jsonl` and `metadata.json` into a joined dataset.

### 2. Join With Replays

```text
replay (what happened)  +  artifact (what policy thought)  =  learning dataset
```

Join keys: `episode_id`, `phase/round/tick`, `seat/slot`.

The replay tells what actually happened and what score resulted. The artifact
tells what the policy saw, what features it parsed, and why it chose its action.

### 3. Mine, Hypothesize, Verify

With the joined dataset, the analysis (decision-path distribution, fallback
trigger rate, LLM failure rate, action quality by path, per-opponent / seat
patterns) and the hypothesis it feeds are covered by `replay-artifact-analysis`,
`replay-variance-miner`, and `policy-hypothesis-loop`. Before any large eval,
smoke-test that artifacts are actually written and join to the replay
(`data-collection-design`).

## Artifact Safety Rules

- Never let artifact writes crash the policy. Wrap all artifact code in
  try/except and record errors to `errors.jsonl`.
- Validate action/observation shapes before deriving fields.
- Keep artifacts under 200 MB. Use parquet/sqlite for large tabular data.
- Write artifacts on ALL paths: normal completion, LLM failure, fallback, and
  timeout. Fallback-path artifacts are often the most valuable.
- Include schema version in metadata so analysis code can handle format changes.

## See Also

- `replay-artifact-analysis` — joining replays with artifacts.
- `hosted-xp-evals` / `coworld-operations` — creating evals + downloading results.
- `base-optimizer-framework` — the full loop.
- `crewrift-optimization` — crewborg's concrete `trace.db` artifact (the
  Crewrift instance of this contract).
