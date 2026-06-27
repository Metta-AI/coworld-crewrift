# Local-run CLI reference

Exact behaviour of the `coworld` commands this skill uses (source `Metta-AI/metta`:
`packages/coworld/src/coworld/`, and `coworld <cmd> --help`). **Re-verified 2026-06-27:**
`download`/`run-episode`/`replay`/`play` all present; since the original 0.1.20 pass
`run-episode` **gained** `-n/--episodes`, `--variant`, and `--use-bedrock`/`--aws-*`.
Re-check with `--help` if a flag seems off — the CLI ships ahead of the metta checkout.
There is no `--version` flag; `uv pip show coworld`.

## `coworld download <ref> [-o DIR=./coworld] [--server] [--refresh]`

- `<ref>` = a `cow_…` id (stable; no auth) **or** a Coworld **name** (resolved to the
  current canonical version; **needs `softmax login`**). Names are not stable.
- Produces under `./coworld/<cow_id>/`: `coworld_manifest.json` (every `image` field
  rewritten to a **local** docker tag like `coworld/<slug>/<name>-<ver>-<i>:downloaded`),
  `coworld_images.json` (public→local tag map), and a canned `AGENTS.md`.
- Side effect: `docker pull` + `docker tag` each referenced image → needs **Docker +
  network**. Idempotent: skips re-pull if manifest+images JSON exist and no `--refresh`.

## `coworld run-episode <manifest> [PLAYER_IMAGE...] [--run TOK]... [-o DIR] [-n EPISODES] [--variant ID] [--timeout-seconds 3600] [--verify-replay] [--use-bedrock] [--aws-profile P] [--aws-region R] [--secret-env K=V]... [--server]`

- `<manifest>` = a path, URL, or bare `cow_…` (auto-downloads+caches if absent).
- **Positional player image(s)** are how you run *your* policy:
  - **one image** → reused for every slot (self-play); **N images** → one per slot (must
    be exactly 1 or slot-count).
  - **zero images** → the manifest's **certification (reference) players** run — the
    *silent fallback*; your change is not under test.
  - `--run` (repeatable, one token per flag, e.g. `--run python --run -m --run mod`)
    overrides the container argv for the supplied image(s); **it requires at least one
    image positional** (`--run` alone errors).
- Default config = the manifest's `certification.game_config` — deliberately
  tiny/degenerate (a "package smoke test, not a benchmark"); **a 0 score is not a
  failure**. For a fuller game headlessly, pass **`--variant <id>`** (added to
  `run-episode` since 0.1.20 — it used to be `play`-only), or supply an
  `episode_request.json` positional with the variant's `game_config`.
- **`-n/--episodes N`** runs N local episodes back-to-back in one invocation (added since
  0.1.20) — use it to confirm the player is **stable across repeated games** (catches an
  intermittent crash / connect-race / timeout that a single smoke would miss). Still
  self-play on the local config, so it is **not** a competitive measure — that's experience
  requests.
- **`--use-bedrock` [`--aws-profile P` / `--aws-region R`]** smoke-tests the LLM path
  locally with **your own** AWS creds — there is **no sidecar locally**, so it proves the
  code can call Bedrock but **not** that the hosted upload is correct (the hosted sidecar
  contract is the [Bedrock section of `coworld-platform.md`](../../../docs/reference/coworld-platform.md#bedrock--in-pod-llm)).
- **Output dir** = `--output-dir` if given, else `./coworld/<cow_id>/results` for a
  downloaded coworld, else `<manifest_dir>/results`. Writes: `config.json`,
  `results.json` (validated vs `game.results_schema`; has a `scores` array),
  **`replay`** (raw bytes, *no extension*), `logs/game.stdout.log`,
  `logs/game.stderr.log`, `logs/policy_agent_<slot>.log` (per player container).
- **Success / crash detection (the Gate-1 signal):** the CLI exits non-zero if the game
  container exits non-zero, **any player container exits non-zero** ("did my player
  crash"), health times out, the player token is rejected, or `results.json` fails
  schema validation. Exit 0 + valid results + replay written = pass.
- On finish it prints `Artifacts:/Results:/Replay:/Logs:` and a ready-to-paste
  `Inspect replay: uv run coworld replay <manifest> <replay>`.

## `coworld play <manifest> [PLAYER_IMAGE...] [--run TOK]... [--variant ID] [--open-browser/--no-open-browser] [-o DIR] [--server]`

- Same player-image / `--run` model as `run-episode`, plus `--variant <id>` to pick a
  non-certification variant. Prints per-slot browser URLs + the global viewer + admin
  client, opens the global viewer, and keeps the session alive until the game exits.
- Skips the token-rejection / health probes and replay verification that `run-episode`
  does. Use it to **watch live**; use `run-episode` for headless artifacts.

## `coworld replay <manifest> <replay> [--open-browser/--no-open-browser] [--timeout-seconds 60] [--server]`

- **Two positionals: manifest then replay file.** Boots the game image in replay mode
  (`COGAME_LOAD_REPLAY_URI`), waits for `/healthz`, opens
  `http://127.0.0.1:<port>/client/replay`. Game container only — no player containers.
- (`coworld replay-open <episode_request_id> [--hosted]` is the counterpart for a
  *stored* episode — fetches one game's replay by its episode-request id; `--hosted`
  prints an Observatory viewer URL with no local Docker. Not for local runs.)

## Gotchas

- **linux/amd64 mandatory** (`runner.py` `_assert_linux_amd64_image`) for every game and
  player image — arm64 aborts with the rebuild hint. Build `--platform linux/amd64`.
- **`--run` / silent-fallback:** no positional image ⇒ reference player runs; `--run`
  without an image ⇒ error.
- **Game image must be local/pullable.** A manifest pointing at an unresolved backend id
  (`img_<uuid>`) aborts telling you to `coworld download … --refresh`.
- **Replay file is named `replay`** (no extension); `coworld replay` wants `<manifest>
  <replay>` in that order.
- **Local ≠ hosted:** local `run-episode` writes plain files only — no episode bundle,
  no commissioner/reporter/grader, no zlib-compressed replay (those are hosted-only).
- **Rotating ids:** `cow_…` is stable; a **name** resolves to whatever is canonical now,
  landing artifacts under a new `./coworld/<new_id>/` when the canonical version changes.
- A `coworld-local` Docker network is created/reused; first run may create it.
