# Build & upload — CLI + API reference

Exact behaviour of the `coworld upload-policy` command and the Observatory routes this skill uses.
**Verified live 2026-06-27** (`coworld upload-policy --help`, `/observatory/openapi.json`). Re-check
with `--help`; the CLI ships ahead of the metta checkout. Auth: `softmax login`
(`load_current_token`); CLI sends `Authorization: Bearer`.

## build — `docker build --platform linux/amd64 -f crewborg/coworld/Dockerfile -t <tag> players/crewborg`

- **linux/amd64 mandatory** — the runner (`runner.py:_assert_linux_amd64_image`) hard-fails on arm64.
- The Dockerfile copies the `crewborg` package and sets `CMD ["python","-m","crewborg.coworld.policy_player"]`;
  the SDK is installed from the public `Metta-AI/coworld-tools` repo (see `crewborg/coworld/Dockerfile`).
- A running **Docker daemon** is required for both build and upload.

## upload — `coworld upload-policy <IMAGE> --name/-n NAME [--run TOK]... [--secret-env K=V]... [--tag K=V]... [--use-bedrock] [--bedrock-model ID] [--server]`

- `<IMAGE>` (positional, required) = a **local** docker image tag (not a registry URI). The client
  `docker image save`s it, hashes it, and pushes to a Softmax-managed ECR via raw OCI calls (a
  deliberate workaround for a Docker-29 + ECR bug).
- **`--name/-n`** (required) = the **stable policy name** the version history hangs off. Re-uploading
  the same name creates a **new version** (server auto-increments `vN`).
- **`--run`** (repeatable, one token per flag, e.g. `--run python --run -m --run crewborg.coworld.policy_player`)
  = argv for images that bundle multiple Coworld roles. **Must launch crewborg's entrypoint**, else a
  reference player runs (the silent no-op). Persisted on the version.
- **`--secret-env K=V`** (repeatable) = environment variables for policy execution, stored in AWS
  Secrets Manager. This is how crewborg's **LLM toggles** are injected (below).
- **`--tag K=V`** (repeatable) = private bookkeeping tags (e.g. `--tag purpose=llm-test`); not behavior.
- **`--use-bedrock`** = sets `USE_BEDROCK=true` in the policy environment. **`--bedrock-model ID`** =
  sets `BEDROCK_MODEL` (requires `--use-bedrock`).
- Routes: `POST /v2/container_images/upload` (+ `/complete`) for the image, then
  **`POST /stats/policies/docker-img/complete`** `{name, container_image_id, run?, policy_secret_env?}`.
  Returns `PolicyVersionResponse {id (pv UUID), name, version, pools, submit_error}`. The CLI prints
  only `Upload complete: <name>:v<version>`.
- **Inert:** uploading enters no competition — it only registers a version. (`resolve-and-upload` is a
  *Coworld/game* upload wrapper — `POST /v2/coworlds/upload` — **not** a policy flow.)

### crewborg LLM env recipe

crewborg's LLM layers are **opt-in (off by default)**. Enable them at upload with:

- `--use-bedrock` (+ optional `--bedrock-model`) — backend access. In a hosted pod crewborg gates on
  the sidecar's injected `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, not on `USE_BEDROCK` — see the Bedrock
  section of [`coworld-platform.md`](../../../docs/reference/coworld-platform.md#bedrock--in-pod-llm).
- `--secret-env CREWBORG_LLM_MEETINGS=1` and/or `--secret-env CREWBORG_LLM_COMMANDER=1` — crewborg's
  own toggles (both default off). Tuning vars (`CREWBORG_LLM_MODEL`, `CREWBORG_LLM_MAX_TOKENS`,
  `CREWBORG_LLM_TEMPERATURE`, `CREWBORG_LLM_TIMEOUT_SECONDS`, `CREWBORG_LLM_TRACE_RAW`) are the same
  mechanism — full table in [`crewborg/README.md`](../../../crewborg/README.md).

## version log — listing uploaded versions

No `coworld versions` command. List every uploaded version for a name via
`GET /stats/policy-versions?mine=true&name_exact=<NAME>&limit=100` → `{entries:[{id, name, version,
created_at}], total_count}` (the `versions.py` script does this). Key the version log on
`(name, version)` + the immutable version UUID (`id`).

## Gotchas

- **linux/amd64 mandatory** — `upload-policy` hard-fails on arm64.
- **`--run` silent fallback** — the quietest failure: no/incorrect `--run` ⇒ a reference player runs,
  so the version uploads fine but a *different* policy plays. Always launch `crewborg.coworld.policy_player`.
- **Docker daemon required** for the image push.
- **Auth** — 401/403 → `uv run softmax login`.
