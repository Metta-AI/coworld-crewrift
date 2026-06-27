---
name: build-and-upload
description: "Use to build the crewborg image and upload it as a new policy version — the routine, inert, every-iteration action that gives you a runnable artifact to smoke-test and evaluate. Triggers: 'build crewborg', 'upload a new version', 'rebuild and re-upload', 'ship a version for testing', 'upload with the LLM on'. Uploading enters NO competition; submitting to a league is the separate coworld-policy-lifecycle (submit & monitor) skill."
---

# Build & Upload — a new crewborg version

The routine, every-iteration action: **build** the crewborg image and **upload** it as a **new
version**, so you have a runnable artifact to smoke-test (`coworld-local-run`) and measure
(`coworld-experience-requests`). **Uploading is inert** — it registers a version and enters no
competition, so do it freely. (Entering a version into a live league is the gated, rare
**`coworld-policy-lifecycle`** / submit & monitor skill.)

**Announce at start:** "Building crewborg `linux/amd64` and uploading it as a new (inert) version."

## Step 1 — Build `linux/amd64`

```bash
docker build --platform linux/amd64 -f crewborg/coworld/Dockerfile -t crewborg:dev players/crewborg
```

- **amd64 is mandatory** — the cluster + local runner hard-fail on arm64 (build `--platform
  linux/amd64` on Apple Silicon). A running **Docker daemon** is required.
- Equivalent: the `tools/` build script (pins the SDK + game ref centrally).

## Step 2 — Upload as a new version

```bash
uv run coworld upload-policy crewborg:dev --name crewborg \
  --run python --run -m --run crewborg.coworld.policy_player
# -> "Upload complete: crewborg:v<N>"   (a NEW version; INERT, not competing)
```

- **`--name crewborg`** (required) is the **stable policy name** the version history hangs off —
  re-uploading the same name **auto-increments `vN`**.
- **`--run` must launch crewborg's entrypoint** (`crewborg.coworld.policy_player`). Omit it and a
  reference/default player runs — **the quietest failure**: the version uploads fine but a *different*
  policy actually plays. (Needed because the image can carry multiple roles.)
- The client `docker save`s + pushes the image, so a **Docker daemon** is required.

### LLM (Bedrock) upload recipe — *only* if shipping the meeting LLM / commander

crewborg plays **fully deterministically by default**; its LLM layers are **opt-in**. To ship them:

```bash
uv run coworld upload-policy crewborg:dev --name crewborg \
  --run python --run -m --run crewborg.coworld.policy_player \
  --use-bedrock [--bedrock-model <model-id>] \
  --secret-env CREWBORG_LLM_MEETINGS=1 [--secret-env CREWBORG_LLM_COMMANDER=1]
```

- **`--use-bedrock`** sets `USE_BEDROCK=true`; in a hosted episode crewborg routes through the per-pod
  sidecar (it gates on the injected `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, not on `USE_BEDROCK`). See
  the [Bedrock section](../../docs/reference/coworld-platform.md#bedrock--in-pod-llm).
- crewborg's own toggles are **env vars**, injected with **`--secret-env`**: `CREWBORG_LLM_MEETINGS=1`
  (meeting chat/votes), `CREWBORG_LLM_COMMANDER=1` (gameplay commander) — both **off** by default.
  Full toggle list (model, tokens, temperature, timeout, trace) is the env-var table in
  [`crewborg/README.md`](../../crewborg/README.md).
- **After the eval, confirm the LLM actually fired** — check the telemetry artifact for
  `domain.meeting_llm_decision` (vs `_fallback`); a silent fall-back to deterministic play is the
  common trap. See the Bedrock debugging table in `coworld-platform.md`.

## Step 3 — Versioning (do this on *every* upload)

Clear versioning is the whole point of uploading: **map each version → the change it carries**, so you
(and a future agent) always know what `crewborg:vN` is testing or capable of.

- **Record `vN → its change`** in the player's [`version_log.md`](../../crewborg/version_log.md).
- **Reconcile** against the live list (there's no `coworld versions` command):

  ```bash
  uv run python skills/build-and-upload/scripts/versions.py --name crewborg   # vN + UUID + created_at
  ```
- Key the log on `(name, version)` with the immutable **version UUID** as the canonical id. Use
  **`--tag KEY=VALUE`** on upload for private bookkeeping (e.g. `--tag purpose=llm-test`).

## Then what

1. **Gate-1 smoke** the new version — `coworld-local-run`.
2. **Measure it** vs the field — `coworld-experience-requests`.
3. **Only when demonstrably better + the human approves** — submit it (the gated
   `coworld-policy-lifecycle` / submit & monitor skill).

## Notes

- **`resolve-and-upload` is NOT this flow** — that's a Coworld/*game* upload wrapper, not a policy one.
- Auth: `softmax login`. Full flags + routes + the LLM env recipe: [`references/cli.md`](references/cli.md).
- The "upload freely, submit rarely" discipline is in [`../../docs/best_practices.md`](../../docs/best_practices.md).
