# Coworld Platform Contract (reference for crewborg)

The platform that Crewrift and **crewborg** run on. This is a focused reference
for an agent optimizing crewborg: what a Coworld is, the player image contract
crewborg must satisfy, how the manifest and lifecycle work, what artifacts come
out, and how in-pod Bedrock LLM access is gated. It links out for deep detail
rather than restating it.

> 🔌 **Want to use an LLM?** It's **optional** (crewborg plays fine deterministically), but its
> meeting + commander Bedrock path is the single most error-prone part of the platform → jump to
> **[Bedrock — in-pod LLM](#bedrock--in-pod-llm)**. The *One Rule* (route through
> `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, **InvokeModel not Converse**) and a **403 / silent-fallback
> debugging table** live there.

---

## How to use / re-verify this doc

**The platform contract is owned by metta, not by this repo.** It is defined and
enforced in the `packages/coworld` package of the **metta** repository, which is
a *separate* git checkout (`~/coding/metta`) that evolves independently of
coworld-crewrift. This doc is a snapshot-with-citations; when a claim matters,
re-derive it from the cited source.

- **metta is READ-ONLY and may be stale.** Before relying on anything you read
  there, run `git -C ~/coding/metta pull` (this is the one mandatory mutation;
  never branch, edit, or commit in that checkout). A stale checkout silently
  produces wrong conclusions.
- **Authoritative platform sources** (paths relative to
  `metta/packages/coworld/src/coworld/docs/`):
  - `README.md` — role model + artifact flow
  - `COWORLD_MANIFEST.md` — manifest authoring semantics
  - `LIFECYCLE.md` — local + hosted episode lifecycle
  - `BEDROCK.md` — hosted Bedrock contract for players
  - `roles/PLAYER.md`, `roles/GAME.md`, `roles/COMMISSIONER.md`, `roles/GRADER.md`, `roles/REPORTER.md`
  - `artifacts/README.md` + `artifacts/*.md` — per-artifact contracts
  - The machine-readable contract is the generated JSON Schema:
    `metta/packages/coworld/src/coworld/coworld_manifest_schema.json`, generated
    from `metta/packages/coworld/src/coworld/types.py`. **Do not trust prose over
    the schema** — the schema is the source of truth for field shapes.
- **This Coworld's concrete instance:**
  `coworld-crewrift/coworld_manifest.json`, plus `coworld-crewrift/README.md`,
  `Dockerfile`, `config.json`, and the `reporters/`, `grader/`, `commissioner/`
  directories.
- **Cited commit context for this snapshot:** metta `main` @ `a01104ee2e`
  (pulled 2026-06-26); crewrift `coworld_manifest.json` game version `0.1.40`.
  If those have moved a lot, re-read before trusting fine detail.

When metta docs and code disagree, treat it as a finding and check the schema /
runner code under `metta/packages/coworld/` before relying on either.

**Related crewborg references:** [`./crewrift-gameplay.md`](./crewrift-gameplay.md),
[`./crewrift-protocol.md`](./crewrift-protocol.md),
[`./crewrift-replays.md`](./crewrift-replays.md), [`./README.md`](./README.md),
and [`../best_practices.md`](../best_practices.md).

---

## What a Coworld is

A **Coworld** is a game-centered development loop packaged as a container set
plus a manifest. Concretely it is:

- one **game** container (the rules engine + WebSocket server + browser clients +
  results/replay writer),
- one or more **player** containers that connect to the game and choose actions,
- supporting role containers (commissioner, grader, reporter, …) that schedule or
  consume episodes,
- a `coworld_manifest.json` that maps the package: which images implement which
  roles, which variants exist, which docs/schemas apply, and the certification
  fixture.

The manifest is the *map* of the Coworld, not the whole thing. (metta
`packages/coworld/src/coworld/docs/README.md`, "What Is A Complete Coworld?")

For crewborg, the Coworld is **crewrift**: a social-deduction game (crewmates +
imposters, tasks, voting, betrayal) defined in `coworld-crewrift/`. crewborg is a
*submitted policy* — a player image that substitutes for the manifest's bundled
player at league time.

### The seven roles

Three roles run *during* an episode; four consume episode artifacts *after*.
(metta `docs/README.md`, "Roles")

| Role | When | Status | What it does |
| --- | --- | --- | --- |
| **game** | per episode (WS server) | live | Runs the episode, serves clients, writes results/replay. |
| **player** | per episode (WS client) | live | Connects to the game, acts in one slot. **← crewborg is here.** |
| **commissioner** | per round (WS server) | live (container leagues) | Schedules league-round episodes, ranks memberships. |
| **reporter** | per report request | MVP hosted runner | Turns episode evidence into reports. |
| **grader** | post-episode, on demand | contract defined, runtime pending | Scores how useful/interesting an episode was. |
| **diagnoser** | post-episode, on demand | reserved | Evaluates a target policy, emits advice. |
| **optimizer** | workbench, long-running | reserved | Drives longer-running policy improvement. |

Key boundary: **the game owns episode truth.** Players are clients — they act in
one slot and never modify the game-written results or replay. (metta
`docs/README.md`, "Role Boundaries"; `docs/roles/PLAYER.md`, "How it fits")

---

## The player image contract (what crewborg must satisfy)

This is the contract crewborg lives or dies by. Source: metta
`docs/roles/PLAYER.md` ("Contract"), corroborated by `docs/LIFECYCLE.md` and
`coworld-crewrift/README.md`.

A player runnable is a **short-lived container, started once per player slot** by
the episode runner. It must:

1. **Read `COWORLD_PLAYER_WS_URL`** from the environment — a fully-formed
   WebSocket URL pointing at the game's `/player` route with this slot's `slot`
   and `token` query params already encoded. `COGAMES_ENGINE_WS_URL` is also set
   to **the same value** for compatibility with older players; prefer
   `COWORLD_PLAYER_WS_URL`. The runner sets both vars to the identical
   `player_ws_url` — verified in code: metta
   `packages/coworld/src/coworld/runner/kubernetes_runner.py` emits adjacent
   `COWORLD_PLAYER_WS_URL` and `COGAMES_ENGINE_WS_URL` env vars (hosted), and
   `runner/runner.py` / `play.py` do the same locally. (metta
   `docs/roles/PLAYER.md`; `docs/LIFECYCLE.md` steps 10 & 12 — step 12 names
   both vars explicitly; `coworld-crewrift/README.md` "The runner starts every
   policy with a `COWORLD_PLAYER_WS_URL`…")
2. **Connect and speak the game-defined player protocol** (Crewrift uses the
   sprite_v1 protocol — `game.protocols.player` in the manifest points at
   `Metta-AI/bitworld/.../sprite_v1.md`; see [`./crewrift-protocol.md`](./crewrift-protocol.md)).
   Observations flow from the game, actions flow from the player, until the
   episode ends.
3. **Act only for its own slot.** The runner gives each container its own
   slot/token; a player must not try to drive other slots.
4. **Exit cleanly when the episode ends / the runner stops it.** crewrift's
   bundled `notsus` player is launched with `--exit-on-disconnect` for exactly
   this (`coworld-crewrift/coworld_manifest.json` `player[0].run`).

**Architecture:** the image is **amd64/linux** (hosted runs are amd64 Kubernetes
pods; crewrift's `Dockerfile` builds a Linux x64 binary). Build/test the player
as amd64 even on an arm64 Mac.

**Entrypoint / `run`:** the manifest's runnable `run` array is the argv the
runner executes (e.g. notsus: `["/bin/notsus", "--exit-on-disconnect"]`). For a
*submitted policy* the run command is supplied at upload time via repeated
`--run` tokens (`coworld upload-policy <image> --run … --run …`), not from this
manifest. (metta `docs/roles/PLAYER.md`, "Bundled players vs submitted policies";
`docs/BEDROCK.md` upload example)

**Optional player artifact (debug only):** at episode end the player MAY upload a
single `.zip` (≤ 200 MB) to the presigned URL in
**`COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`**. If the var is absent, skip it. Upload
*before* the bounded teardown window or it is lost; the platform never blocks
teardown for it, and a missing artifact never fails an otherwise good episode.
This is the only file a player authors directly — it never touches game-owned
results/replay. (metta `docs/roles/PLAYER.md`; `docs/artifacts/PLAYER_ARTIFACT.md`)

**Resources (hosted):** each player pod is scheduled at 250m CPU / 256Mi memory
(a *request*, not a hard limit). (metta `docs/roles/PLAYER.md`, "Contract";
`docs/roles/GAME.md`, "Hosted runtime resources")

**Bundled player vs submitted policy** — same runtime contract, different upload
path and image visibility:
- *Bundled players* (`manifest.player[]`, uploaded via `coworld upload-coworld`)
  get mirrored to public ECR — treat as fully public, no secrets in the image.
- *Submitted policies* (crewborg, uploaded via `coworld upload-policy`) stay
  private to Observatory runtime and substitute for the bundled player at league
  episode time. (metta `docs/roles/PLAYER.md`, "Bundled players vs submitted policies")

> **Failure mode to remember:** a player that blocks (e.g. on a slow LLM call)
> and never submits a valid action lets the episode run to timeout, and a
> timed-out episode scores as a **loss** regardless of play quality. Always
> submit a valid action before the deadline. (metta `docs/BEDROCK.md`, "Be
> robust to throttling")

---

## The manifest structure

`coworld-crewrift/coworld_manifest.json` is the package map. **Verified top-level
sections actually present in this repo's manifest** (read 2026-06-26):

| Section | Present? | Shape | Notes |
| --- | --- | --- | --- |
| `$schema` | yes | string | Points at `coworld_manifest_schema.json` on `Metta-AI/coworld` main. |
| `game` | yes (**required**) | object | Single game runnable + metadata + schemas + docs. |
| `player[]` | yes (**required**, ≥1) | array | Only `notsus` is bundled here. |
| `commissioner[]` | yes (optional) | array | `among-them-commissioner` (shared social-deduction commissioner). |
| `grader[]` | yes (optional) | array | `crewrift-grader`. |
| `variants[]` | yes | array | One variant: `default` (8-player). |
| `certification` | yes | object | 8× `notsus`, a fast token-free `game_config`. |
| `reporter[]` | **absent** | — | Optional; not declared in this manifest (a reporter exists in `reporters/eventlog/` but isn't wired into the manifest). |
| `diagnoser[]` | **absent** | — | Optional / reserved. |
| `optimizer[]` | **absent** | — | Optional / reserved. |

Schema rule (metta `docs/COWORLD_MANIFEST.md`, "Role Sections"): only `game` and
`player` are **required** today; `commissioner`, `reporter`, `grader`,
`diagnoser`, `optimizer` are optional. `diagnoser`/`optimizer` carry
`x-coworld-future-required: true` in the schema. **Always confirm the exact field
list against `coworld_manifest_schema.json` in metta — prose here can lag.**

### Inside `game` (verified against the crewrift manifest)

- `name` / `version` / `description` / `owner` — game identity (`crewrift`,
  `0.1.40`, owner `treeform@softmax.com`).
- `runnable` — `type:"game"`, `image` (ECR digest-pinned), `run`
  (`["/bin/crewrift"]`), `source_url` (a **40-hex commit SHA**-pinned GitHub
  tree; certification rejects branch/tag refs — metta `docs/COWORLD_MANIFEST.md`
  "Images, Runnables, And Releases").
- `config_schema` — JSON Schema for the runtime config the game reads from
  `COGAME_CONFIG_URI`. **Must** declare a string-array `tokens` field with
  `minItems`/`maxItems` (here 8–16) for runner-injected player auth. Crewrift's
  schema also exposes all gameplay knobs (`imposterCount`, `killCooldownTicks`,
  `tasksPerPlayer`, `voteTimerTicks`, map path, etc.). (metta
  `docs/COWORLD_MANIFEST.md` "Game Configs, Tokens, And Player Names";
  `docs/roles/GAME.md` "Player slots")
- `results_schema` — JSON Schema validating the `results.json` the game writes to
  `COGAME_RESULTS_URI`. Cross-game tooling requires a numeric **`scores`** array,
  one per slot. Crewrift adds `names`, `win`, `tasks`, `kills`, `imposter`,
  `crew`, `vote_players`, `vote_skip`, `vote_timeout`, `connect_timeout`,
  `disconnect_timeout` — these are the per-slot signals crewborg's analysis tools
  read. (metta `docs/COWORLD_MANIFEST.md` "Results Schema";
  `coworld-crewrift/coworld_manifest.json` `game.results_schema`)
- `protocols.player` / `protocols.global` — URI refs to the sprite_v1 protocol
  doc.
- `docs.readme` (**required**) + `docs.pages[]` — onboarding/rules/strategy refs.

### Tokens are runner-injected, never authored

Author-written configs are **token-free**: `variants[].game_config` and
`certification.game_config` contain no `tokens`. The runner generates one fresh
token per slot and writes the concrete per-episode config at launch. (You can see
the *concrete* shape with injected tokens in `coworld-crewrift/config.json`, a
local dev config with literal `0xBADA55_*` tokens — that file is the
*post-injection* form, not a manifest config.) (metta `docs/COWORLD_MANIFEST.md`
"Game Configs, Tokens, And Player Names"; `docs/roles/GAME.md` "Player slots")

### Variants & certification

- `variants[]` — named, token-free `game_config`s defining episode setups.
  Crewrift's `default` variant is 8 players, 2 imposters, closed roster.
- `certification` — a `players[]` roster (8× `notsus`) + a fast token-free
  `game_config` that `coworld certify` runs as a one-episode smoke test (short
  timers, `tasksPerPlayer:1`, `maxTicks:300`). It proves the package wires up;
  it is **not** a gameplay benchmark. (metta `docs/LIFECYCLE.md` "Certification")

### Secrets in the manifest

Manifest runnable `env` is **public** (uploaded manifests + downloaded packages
expose it). Never put raw keys there. For hosted *game-container* secrets, upload
via `coworld secret put` and reference `secret://coworld/<name>/<secret>`
symbolically. Player/policy secrets go through the upload path instead (see
Bedrock below). (metta `docs/COWORLD_MANIFEST.md` "Hosted Episode Game Secrets";
`docs/roles/PLAYER.md` "Secrets, Bedrock, and LLM credentials")

---

## Lifecycle & the runner

Source: metta `docs/LIFECYCLE.md` and `docs/README.md` ("Artifact Flow").

### Per-episode launch sequence

For each scheduled episode the runner:

1. resolves the game runnable + one player runnable per slot,
2. generates one token per slot and writes the concrete game config,
3. starts **one game container** listening on `COGAME_HOST:COGAME_PORT`
   (default `0.0.0.0:8080`) with routes `/healthz`, `/player`, `/global`,
   `/client/*`,
4. waits for `GET /healthz` → 200, then verifies an invalid player token is
   rejected,
5. starts **one player container per slot**, each with its own
   `COWORLD_PLAYER_WS_URL` (and `COGAMES_ENGINE_WS_URL`) pointing at `/player`
   with that slot's `slot`+`token`,
6. waits for the players and game to exit,
7. validates `results.json` against `game.results_schema`, captures logs and
   replay.

(metta `docs/LIFECYCLE.md` "Local Development Lifecycle" steps 1–14;
`docs/README.md` "During The Episode")

### Local vs hosted — two runners, one contract

The **player/game protocol is identical** in both. What differs is orchestration
(metta `docs/LIFECYCLE.md` "Key Differences"):

- **Local** (`coworld run-episode` headless, `coworld play` browser) — `coworld`
  CLI + local Docker on the `coworld-local` network, artifacts written to a local
  workspace. This is crewborg's Gate-1 smoke test: did it connect → play → exit?
- **Hosted tournament** — Observatory/platform + Kubernetes. Each episode is a
  parent Job (init + game + worker containers sharing an `emptyDir`); the worker
  creates a ClusterIP Service for the game and **one child pod per player slot**.
  Submitted policy versions fill the slots. **20-minute Job active deadline.**
  (metta `docs/LIFECYCLE.md` "Hosted Tournament Lifecycle" steps 6–17;
  `docs/roles/GAME.md` "Hosted runtime resources")

### League rounds vs experience requests

Hosted episodes are created two ways (metta `docs/LIFECYCLE.md` "Hosted
Tournament Lifecycle"):

- **League rounds** — the platform decides a round is due, starts the
  **commissioner** container, connects to its `/round` WebSocket; the
  commissioner sends `schedule_episodes`, episodes run, each `episode_result`
  (with `scores`) routes back, and the commissioner emits `round_complete` with
  per-division rankings + membership changes. crewrift uses the shared
  `among-them-commissioner` (`manifest.commissioner[0]`).
- **Experience requests** (`coworld xp-request create`) — a player author
  directly fans out a batch of pool-less episodes against a chosen roster.
  Episodes start `pending` and dispatch asynchronously, then run the *same*
  hosted episode job and produce the *same* artifacts. This is crewborg's primary
  A/B and evaluation mechanism.

The commissioner `id` must match `commissioner_config.commissioner_runnable_id`
when a league is seeded. (metta `docs/COWORLD_MANIFEST.md` "Role Sections")

---

## Artifacts

The game owns episode truth; everything below flows from it. Source: metta
`docs/artifacts/README.md` and the per-artifact pages.

**Per-episode (success-critical first):**

| Artifact | Producer | Local | Hosted |
| --- | --- | --- | --- |
| **Results** (`scores[]` + crewrift fields) | game | `results.json` | `RESULTS_URI` |
| **Replay** (game-defined bytes; crewborg's main behavioral evidence) | game | `replay` | `REPLAY_URI` → `replay.z` (zlib) |
| Game logs | game/runner | `logs/game.*.log` | `DEBUG_URI` |
| Player logs (per slot) | player/runner | `logs/policy_agent_{slot}.log` | `POLICY_LOG_URLS` |
| **Player artifact** (optional `.zip`, ≤200MB) | player | `policy_artifact_{slot}.zip` | `PLAYER_ARTIFACT_UPLOAD_URLS` → `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` |
| Error info | hosted runner | — | `ERROR_INFO_URI` |
| Episode bundle | bundling layer | on-demand `.zip` | on-demand `.zip` |

(metta `docs/artifacts/README.md` "Episode Artifacts"; `docs/LIFECYCLE.md`
"Hosted output artifacts"; `docs/artifacts/PLAYER_ARTIFACT.md")

Key points for crewborg:
- **Results + replay are the only success-critical artifacts.** Player logs and
  the player artifact are diagnostic; the game's results/replay are the source of
  truth. Container stdout/stderr is public diagnostic output, not authoritative.
  (metta `docs/roles/PLAYER.md` "Logging and artifacts"; `docs/roles/GAME.md`
  "Logging")
- **Bundling is consumption-time.** The runner does not assemble a bundle;
  graders/diagnosers later receive one via `COGAME_EPISODE_BUNDLE_URI`, reporters
  receive direct artifact refs over `/reporter`. Player artifacts are
  policy-scoped: you only get back slots your policy version owns (team members
  see all). (metta `docs/README.md` "After The Episode";
  `docs/artifacts/PLAYER_ARTIFACT.md` "Visibility")
- To pull these for analysis, use the crewborg-side skills/tooling and the
  `coworld episode-logs` / `coworld replay-open` CLI surfaces (metta
  `docs/artifacts/PLAYER_ARTIFACT.md` "The CLI uses those routes").

**Supporting-role outputs:** reports (reporter), grades (`COGAME_GRADE_URI`,
grader), diagnoses, optimizer outputs, round decisions (commissioner). (metta
`docs/artifacts/README.md` "Supporting-Role Outputs")

---

## Bedrock — in-pod LLM

**Bedrock is optional — crewborg plays fully without it.** The **meeting LLM** (`CREWBORG_LLM_MEETINGS`)
and **gameplay commander** (`CREWBORG_LLM_COMMANDER`) are **opt-in (off by default)** and fall open to
deterministic play, so the deterministic policy needs none of this. **If you do want to use an LLM**,
it calls AWS Bedrock in hosted episodes through a per-pod **sidecar** that holds the real identity and
signs the calls — so no API keys ever ship in the player. That path is the single most error-prone
part of the platform, so read the contract + debugging here *before* turning the LLM on. Authoritative
source: metta `docs/BEDROCK.md` (the player runtime contract) + the runner wiring cited under "How the
access is delivered" below; crewborg's own gating is in `crewborg/strategy/meeting/llm.py` +
`crewborg/strategy/commander/llm.py`.

> ### ⚠️ THE ONE RULE (if you use Bedrock) — send every call to `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`
> A hosted player pod is given **`AWS_ENDPOINT_URL_BEDROCK_RUNTIME`** (e.g. `http://127.0.0.1:<port>`).
> **Every Bedrock call must go to that endpoint.** Send to the real AWS host instead and your call
> carries the platform's **placeholder credentials → HTTP 403 → the episode silently falls back to a
> non-LLM baseline** (nothing wrong in the score, nothing pointing at the cause). Standard SDKs
> (`boto3`, `AnthropicBedrock`, the AWS JS SDK, `@cogweb/llm`) read this env var **automatically**;
> only hand-rolled HTTP must read it. Never hardcode the host or port. Two corollaries:
> - **Use `InvokeModel`, not `Converse`** — the runner identity grants `bedrock:InvokeModel` only;
>   `Converse` returns `AccessDenied`.
> - **Don't supply real creds and don't sign** — the sidecar strips your auth and re-signs; the
>   `bedrock-sidecar` placeholder creds in your env are deliberately fake.
> - **Gate "LLM available" on the ENDPOINT env var's presence — NOT on `USE_BEDROCK`** (the sidecar
>   path does not set it). This is exactly what crewborg's factories do.

### Injected env (a hosted, sidecar-backed pod)

| Env var | Value | What to do with it |
|---|---|---|
| `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` | the sidecar, `http://127.0.0.1:<port>` | **Send all Bedrock calls here**; gate "LLM on" on its presence. |
| `BEDROCK_MODEL` | the id from `--bedrock-model` | **Read your model from this** — never hardcode an id. |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | the Bedrock region | the SigV4 region (the SDK reads it). |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_BEARER_TOKEN_BEDROCK` | `bedrock-sidecar` (placeholder) | leave as-is — stripped + re-signed by the sidecar; they never reach AWS. |

### Debugging LLM play — start here

crewborg's LLM is **opt-in and fails open** to deterministic play, so a broken LLM is a *silent
fallback*, not a crash. **First, is it firing?** Check the player-artifact telemetry
(`artifacts/policy_artifact_<slot>.zip` → `telemetry.jsonl`):
- `domain.meeting_llm_decision` events present (good) vs `domain.meeting_llm_fallback` (LLM not reached);
- `domain.commander_call {outcome:"ok"}` vs errors; and `domain.commander_started {env_seen:…}` — if
  `env_seen.AWS_ENDPOINT_URL_BEDROCK_RUNTIME` is false, **the sidecar isn't attached** (the empty-endpoint row below).

**Easy problems — fix from the table.** *Always log the response BODY, not just the status code* — the
Bedrock error body names the exact failure (route vs action vs model):

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 403` (`UnrecognizedClientException`, bad token/signature) on every call | hitting the **real AWS host** with placeholder creds (bypassing the sidecar) | send to `$AWS_ENDPOINT_URL_BEDROCK_RUNTIME`; **log the exact URL** you POST to |
| `AccessDenied` for `bedrock:Converse` | you used the **Converse** API | switch to **InvokeModel** (`/model/{id}/invoke` with the Anthropic Messages body) |
| `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` empty / unset | sidecar not attached: coworld not Bedrock-enabled, policy uploaded **without `--use-bedrock`**, or running locally | hosted: fix the upload (`--use-bedrock`) + confirm the coworld is enabled; local: there's no sidecar — use your own creds |
| 0 completed episodes / silent non-LLM baseline in hosted rounds | a failing model call is swallowed → fallback | log the **response body + the endpoint URL** first — it's almost always the 403/route issue above |

Verify reachability **in-pod**: `echo "$AWS_ENDPOINT_URL_BEDROCK_RUNTIME"` (empty ⇒ no hosted Bedrock);
`curl -sS "$AWS_ENDPOINT_URL_BEDROCK_RUNTIME/healthz"` (expect `ok`).

**Complex problems — where to look.** The strip/inject behavior lives in the **runner code, not in
`BEDROCK.md`** — trace `runner/bedrock_sidecar_wiring.py` (`bedrock_app_endpoint_env`,
`RESERVED_SIDECAR_APP_ENV`, `build_bedrock_sidecar`) + `runner/kubernetes_runner.py`
(`_DIRECT_BEDROCK_APP_ENV`, the strip/re-apply loop), mirrored for the hosted dispatcher in
`app_backend/.../job_runner/bedrock_sidecar_wiring.py`. **⚠️ Historical sharp edge:** the sidecar has
been wired for **experience-request** jobs but not always **league/dispatch rounds** — so the meeting
LLM can fire in xreq pods yet fall back in league; confirm per round type via the telemetry above.

### How the access is delivered (the sidecar)

For a **submitted policy** (crewborg), hosted Bedrock access is enabled by
**upload flags**, and the platform runs that pod with the Bedrock service account
instead of needing an API key baked in:

```bash
uv run coworld upload-policy <image> --name "<policy-name>" \
  --run python --run -m --run <module> \
  --use-bedrock \
  --bedrock-model us.anthropic.claude-haiku-4-5-20251001-v1:0
```

- `--use-bedrock` → stores `USE_BEDROCK=true` in the policy env; hosted jobs then
  grant Bedrock access.
- `--bedrock-model MODEL` → sets `BEDROCK_MODEL`. **The player must read its model
  from `BEDROCK_MODEL`** — hardcoding an ID or reading a different var name is a
  classic silent failure. (metta `docs/BEDROCK.md` "Enable Bedrock at upload time")

**How the access is actually delivered (the sidecar).** Hosted Bedrock for a
player is *not* raw AWS credentials in the pod. When the platform runs with the
Bedrock sidecar enabled (`BEDROCK_SIDECAR_ENABLED=true`) and the policy has
`USE_BEDROCK=true`, the runner:

1. **Strips** the user/policy env of all direct AWS/Bedrock vars
   (`USE_BEDROCK`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_ROLE_ARN`,
   `AWS_WEB_IDENTITY_TOKEN_FILE`) and the reserved sidecar keys (incl. any
   user-supplied `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` / bearer token) so a policy
   can't override or bypass the sidecar
   (`runner/kubernetes_runner.py` `_DIRECT_BEDROCK_APP_ENV` +
   `runner/bedrock_sidecar_wiring.py` `RESERVED_SIDECAR_APP_ENV`).
2. **Re-applies platform-owned env last** so it wins: `USE_BEDROCK=true` plus the
   sidecar endpoint env — **`AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://127.0.0.1:<port>`**
   pointing at a loopback Bedrock sidecar container, plus placeholder
   credentials (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_BEARER_TOKEN_BEDROCK`
   = non-functional dummies) and `AWS_REGION`/`AWS_DEFAULT_REGION`
   (`runner/bedrock_sidecar_wiring.py` `bedrock_app_endpoint_env`).
3. Adds a **native sidecar container** (`bedrock-sidecar`, an `initContainer`
   with `restartPolicy: Always`) bound to `127.0.0.1:<port>`. The player's AWS
   SDK signs with the dummy creds and sends to localhost; the sidecar strips that
   auth header and **re-signs with the real IRSA identity** it alone holds, then
   forwards upstream. This is why no real keys ever live in the player pod
   (`runner/bedrock_sidecar_wiring.py` `build_bedrock_sidecar`; mirrored in
   metta `app_backend/src/metta/app_backend/job_runner/bedrock_sidecar_wiring.py`
   for the hosted dispatcher path).

**Net effect for crewborg:** a `--use-bedrock` player pod sees
`USE_BEDROCK=true` and `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` set to a loopback URL.
crewborg's LLM factories gate on the presence of that endpoint var — it is the
platform-injected gate, not a crewborg invention (see
[`../best_practices.md`](../best_practices.md) and the crewborg memory notes).
**The contract metta guarantees:** with `--use-bedrock`, the hosted player can
call Bedrock via the default credential chain (pointed at the sidecar) without
shipping any keys; without it, none of the above env is injected and there is no
access. (Re-verify against the runner code above; this strip/inject logic lives
in the runner, not in `docs/BEDROCK.md`, which only documents the upload flags.)

> **Sharp edge:** a Bedrock player can pass local certification at full score and
> still be **disqualified in its first hosted rounds** if it was uploaded without
> `--use-bedrock` or reads the model from the wrong variable — those episodes
> produce 0 completed episodes / no replay. Check the upload flags and
> `BEDROCK_MODEL` *first* when LLM play silently does nothing. (metta
> `docs/BEDROCK.md`)

### Game Bedrock = on by default in hosted runs

The crewrift **game** container gets Bedrock by default in hosted runs: Softmax
provides credentials + region, and the container sees `USE_BEDROCK=true`,
`AWS_REGION`, `AWS_DEFAULT_REGION` set automatically (the replay container too).
This is hosted-only — local `play`/`run-episode` provide no AWS creds. (metta
`docs/roles/GAME.md` "Bedrock and AWS access")

### Throttling robustness (load-bearing)

Hosted Bedrock capacity is **shared** and can run out under load (throttling /
"Too many tokens per day"). Because a blocked player → timed-out episode → scored
loss, crewborg must: bound every model call (timeout + retry cap), fall back to a
valid default move on error, and **always** submit a valid action before the
deadline. (metta `docs/BEDROCK.md` "Be robust to throttling")

### Local Bedrock testing

Local runs use *your* AWS credentials and prove the code can call Bedrock, but do
**not** prove the upload is correct:

```bash
uv run coworld run-episode <manifest.json> <player-image> \
  --run python --run -m --run <module> \
  --use-bedrock --aws-profile default --aws-region us-west-2
```

This sets `USE_BEDROCK=true` and passes host AWS env into the local player
container. (metta `docs/roles/PLAYER.md` "Secrets, Bedrock, and LLM credentials")

---

## Pointers & re-derivation

- Platform contract source: **metta `packages/coworld/`** (READ-ONLY; `git pull`
  before trusting). Docs under `src/coworld/docs/`, schema at
  `src/coworld/coworld_manifest_schema.json`, models at `src/coworld/types.py`,
  runner under `src/coworld/runner/`.
- This Coworld: `coworld-crewrift/coworld_manifest.json` + `README.md` +
  `Dockerfile` + `config.json` + `reporters/` / `grader/` / `commissioner/`.
- Crewborg gameplay/protocol/replay/practice detail:
  [`./crewrift-gameplay.md`](./crewrift-gameplay.md),
  [`./crewrift-protocol.md`](./crewrift-protocol.md),
  [`./crewrift-replays.md`](./crewrift-replays.md),
  [`./README.md`](./README.md), [`../best_practices.md`](../best_practices.md).
