# Design: expand_replay reporter

**Status:** design / pre-implementation
**Goal:** a minimal reporter that wraps `tools/expand_replay.nim` so a completed
Crewrift episode produces a structured event report, conformant with the
Coworld **reporter** role contract as defined in `metta_3`
(`packages/coworld/src/coworld/docs/roles/REPORTER.md` and
`.../reporter/protocol.py`).

This document is the place to settle the open decisions *before* writing code.
Several things the prompt assumes turn out to be underspecified or to cut
against the current spec; those are called out explicitly below.

---

## Decisions (resolved)

These were worked through one by one and are now locked; the rest of the
document gives the reasoning behind each.

1. **Output format: JSON.** `purpose: categorical_events`,
   `output_format: { mime: "application/json", schema: <rows schema> }`, emit
   the `{ts, player, key, value}` rows as a JSON array via `encoding: "json"`.
   No parquet. (§3a)
2. **Rows source: import `expand_replay` as a library.** Re-add the
   structured-event layer to `tools/expand_replay.nim` (CLI text output stays
   byte-identical) and expose `expandReplayTimeline`; the reporter calls it
   directly — no subprocess, no text parsing, real slots. (§3b option 1)
3. **Language: Nim.** Follows from JSON + library import; reuse `mummy` and
   `bitworld/runtime`, ship one in-repo Docker image. (§3c)
4. **Replay input: `context.replay_uri`** (a `file://`/`https://` URI), with a
   `REPORTER_REPLAY_URI` env var as a local-testing fallback; `report_failed`
   if neither is present. We own this key for now and document it as provisional
   until the platform standardizes input-wiring. Hosted replays arrive
   zlib-compressed (`replay.json.z`) and are decompressed with `zippy`. (§5.1)
5. **`target.kind`: gate on `episode`.** Any other kind → `report_failed` with a
   clear message. (§5.3)
6. **Edge policy: "ran it = success."** A valid replay always yields a
   `report_output` (an empty `[]` when there are no events); a missing /
   unfetchable / undecodable replay, or a hash-validation failure, →
   `report_failed`. (§5.4)
7. **Manifest: defer + document.** Leave `coworld_manifest.json` untouched;
   document the intended `reporter[]` entry in the reporter README and wire it
   when the platform runtime and the new manifest schema land here. (§5.5)
8. **Testing: unit tests + mock WS client.** The platform reporter runtime is
   unshipped, so end-to-end validation is a local mock-client round-trip plus
   unit tests for protocol and extraction; no live-platform integration test is
   possible yet. (§5.2)

---

## 1. What we're building (restated)

A small service, in its own directory under `reporters/`, that:

1. Speaks the metta_3 reporter WebSocket contract (Docker image, `/healthz`,
   `/report`, `report_request` → `report_output`/`report_failed`).
2. On each request: gets the episode's replay, runs `expand_replay` over it,
   and turns the result into the reporter's declared output payload.
3. Keeps `expand_replay` as the one piece that understands a replay; the
   reporter is glue around it.

The intent is a *thin wrapper*, not a second analysis engine.

---

## 2. The contract we must conform to (from metta_3)

Source of truth: `roles/REPORTER.md`, `reporter/protocol.py`,
`coworld_manifest_schema.json` (all on branch `boggsj/coworld-reporter-protocol`).

### Runtime
- Long-lived container. Listen on `0.0.0.0:8080`.
- `GET /healthz` → 200 when ready.
- `WEBSOCKET /report` — all work happens here as JSON messages with a `type`
  discriminator.

### Messages
- **Platform → reporter:** `report_request { request_id, target:{kind,id},
  reason, context:{…} }`, and `drain { reason }`.
- **Reporter → platform:** `report_accepted { request_id }` (optional ack),
  `report_output { request_id, target, mime, encoding, payload? }`,
  `report_failed { request_id, target, error }`.
- `encoding` ∈ `text | json | base64 | binary`. For **binary** (e.g. parquet)
  the `payload` is omitted and the bytes are sent as the **immediately
  following** WebSocket binary frame, back-to-back, under a per-connection send
  lock (the binary frame carries no `request_id`, so correlation lives in the
  preceding control message).

### Inputs
- The reporter **fetches its own inputs over HTTPS**. It is *not* handed an
  episode bundle. `report_request.context` is an **opaque bag of hints** (the
  spec's example shows `results_uri`); there is **no mandated key** for the
  replay.

### Manifest
- Lives in `manifest.reporter[]`. Each entry adds `purpose`
  (`narrative | timeseries | categorical_events`) and `output_format`:
  a bare MIME string, **or** a typed `{ mime, schema }` where `schema` is a
  **JSON Schema** the payload validates against.

### Status of the contract in this checkout
- The platform's `/report` driver and the reporter-service runtime are **not
  shipped** ("runtime pending"). `manifest.reporter[]` entries are *declared but
  not yet invoked*.
- The bundle-request API (the normal way to fetch a replay for an episode) is
  **planned, not implemented**.
- The only live artifact is `reporter/protocol.py` (the Pydantic message
  models). The legacy paintarena reporters are zip-contract, short-lived, and
  explicitly **not** to be copied.

**Consequence:** we can conform to the *message shapes* and unit-test them, but
we cannot integration-test against a real platform yet, and we must *invent* how
the replay URI reaches the reporter. Build to the contract; treat the wiring as
provisional.

---

## 3. The three decisions that actually matter

The prompt implies a specific shape ("expand_replay text → parquet, in Nim").
Each leg of that is a real fork. My recommendation in each case differs from the
literal prompt, with reasons.

### 3a. Output format — JSON, not parquet (recommended)

The prompt says "reporter parquet format." But:

- The `{ts,player,key,value}` **parquet event log is the *legacy* artifact**
  (`artifacts/EVENT_LOG.md` is banner-marked *Superseded*). The current contract
  emits the data as a `report_output` payload in a declared `output_format`.
- The typed `output_format` is `{ mime, **JSON Schema** }`. JSON Schema
  describes JSON/text, **not** binary parquet. Declaring parquet means falling
  back to the bare-MIME string form (`application/vnd.apache.parquet`) with **no
  machine-readable column schema** — i.e. the contract's machine-consumer story
  doesn't actually fit parquet cleanly.
- Writing parquet is the single least-minimal thing we could do. The old scribe
  hand-rolled a **308-line `parquet.nim`**. Nim has no parquet in our vendored
  deps. That fights "minimal" hard.

**Recommendation:** `purpose: categorical_events`,
`output_format: { mime: "application/json", schema: <events schema> }`, emit the
same `{ts, player, key, value}` rows as a JSON array (or NDJSON), `encoding:
json`. Same data, same table shape, fits the contract's validation model, and is
~free in Nim (`std/json`).

**Keep parquet only if** a *named* downstream consumer (Observatory surface,
diagnoser, optimizer) actually requires parquet bytes. If so, that pushes us
toward Python (§3c) rather than reviving the hand-rolled writer.

### 3b. How we get structured rows out of expand_replay — the slot problem

The prompt's plan is: run `expand_replay`, take its **text** output, and parse
that into rows. The problem is that the text was designed for humans and is
**lossy in exactly the field the event-log schema needs most**:

- The `player` column is an integer **slot**. The text never prints slots — it
  prints labels like `red(notsus2)`. To fill `player`, the parser would have to
  reconstruct slot identity from color/address by replaying join order. That's a
  heuristic, and it's a *correctness* risk, not just ugliness.
- Chat is printed via Nim `repr(...)` (quoted, escaped); recovering the raw text
  means un-`repr`-ing it.
- Every line type (`reported body … room …`, `score … +N (for reason)`,
  `completed task N while dead`, `voted skip`) needs a bespoke regex, and the
  parser silently rots the first time a line format changes.

In other words, reverse-parsing the text re-derives all the structure
`expand_replay` already computed internally, *plus* guesses at slots it threw
away. That is more code and more fragile than the alternative.

**Two honest options:**

- **(A) Frozen `expand_replay` + text parser in the wrapper.** Zero change to
  `expand_replay`. Cost: a fragile parser and slot-by-join-order reconstruction.
- **(B) Teach `expand_replay` a structured output mode** (e.g.
  `--format ndjson`) that emits `{ts,player,key,value}` rows directly, with real
  slots. The wrapper then just forwards rows — no parser, no slot guessing.
  Cost: this requires surfacing the structured fields inside `expand_replay`
  (the event-layer work), so it is a real change to that file, not a one-liner.

**Recommendation: (B).** It is the robust minimal *system* even though it is not
the minimal *diff to one file*. If you want `expand_replay` frozen, we take (A)
and accept the parser as documented technical debt — but the `player` slot
reconstruction is the part I'd least want to ship.

> Note: (B) is a smaller, more targeted change than the full refactor we tried
> earlier — it's one additional rendering branch plus the slot/field plumbing,
> with the text output left byte-identical.

### 3c. Language — Nim (recommended) or Python

- **Nim** keeps everything in-repo: reuse `mummy` (already vendored, already
  used by the old scribe service), call `expand_replay` directly, ship one
  Docker image consistent with the rest of the repo. Best fit **if output is
  JSON/text** (§3a). Parquet in Nim = hand-roll = not minimal.
- **Python** makes parquet trivial (`pyarrow`) and lets us import metta_3's
  `reporter/protocol.py` Pydantic models verbatim. Cost: a foreign runtime in a
  Nim repo, and shelling out to the compiled `expand_replay` binary.

**Recommendation: Nim + JSON output.** Choose Python only if §3a resolves to
"parquet is required."

---

## 4. Proposed design (recommended path: Nim, JSON, structured expand_replay)

```
reporters/
  eventlog/                     # new reporter, its own dir
    service.nim                 # mummy WS server: /healthz + /report, protocol loop
    protocol.nim                # report_request/report_output/... encode+decode + validation
    report.nim                  # replay bytes -> expand_replay rows -> JSON payload
    Dockerfile                  # debian + nimby, listens on 8080
    config.nims                 # --path to src
    test_eventlog.nim           # protocol + extraction tests against tests/replays/notsus.bitreplay
    README.md
tools/expand_replay.nim         # + structured `--format ndjson` mode (decision 3b/B)
```

### Request flow
1. Platform polls `/healthz` → 200, connects `/report`.
2. `report_request { request_id, target:{kind:"episode", id}, reason, context }`.
3. Resolve the replay URI (see §5.1), fetch bytes over HTTPS
   (`bitworld/runtime.readCogameUri`, which already does file/https).
4. `expand_replay` the bytes → `seq[row]` (`{ts,player,key,value}`).
5. Send `report_output { request_id, target, mime:"application/json",
   encoding:"json", payload:<rows> }` (or `report_failed` with the reason).
6. Handle `drain` by finishing in-flight work and exiting 0.

### Reuse
- `mummy` server skeleton, healthz, concurrency lock: lift from the old scribe
  service (it already solved the WS plumbing; only the *protocol* changes).
- Replay fetch: `readCogameUri` (file:// and https://) — no new dep.
- Event extraction: `expand_replay` (the one source of truth).

### What's genuinely new
- The metta_3 message protocol (different from scribe's `report.generate`).
- The binary-frame send discipline (only if we ever emit parquet).
- The structured `expand_replay` mode (decision 3b/B).

---

## 5. Underspecified / open questions

1. **Replay URI discovery.** The contract gives no standard `context` key for
   the replay. Proposal: read `context.replay_uri` if present, else fail clearly
   (later: fetch the episode bundle once that API exists). Also support a
   `REPORTER_REPLAY_URI` env override for local testing. **Need your call on the
   context key name**, or confirmation that we own it for now.
2. **No live platform driver.** The reporter runtime isn't shipped, so there is
   nothing to wake this service end-to-end yet. We validate with unit tests + a
   mock WS client (as in the prior smoke test). Acceptable?
3. **`target.kind`.** We only handle `episode`. Reject other kinds with
   `report_failed`? Or ignore `kind` and treat `id` as a replay handle?
4. **Empty / failed episodes.** Emit a well-formed zero-row payload (the spec
   wants no missing-file special case), or `report_failed`? I lean zero-row for
   success-with-no-events, `report_failed` for "no replay".
5. **Manifest version gap.** The crewrift `coworld_manifest.json` reporter entry
   (`default-reporter`) predates `purpose`/`output_format`; it does **not** match
   the metta_3 schema. Do we (a) add a *new* reporter entry in the new shape and
   leave `default-reporter` alone, (b) replace it, or (c) leave the manifest
   untouched for now and wire it when the runtime ships? I lean (c) + document.
6. **Determinism.** JSON output is naturally deterministic; parquet is not
   (writer-metadata in the footer) — another reason to prefer JSON.

---

## 6. Pushback (the short version)

- **"Parquet" is the legacy shape.** The current contract wants a declared
  `output_format` with a JSON Schema; parquet doesn't fit that and is the most
  expensive thing to produce. Recommend JSON unless a named consumer needs
  parquet bytes.
- **Parsing expand_replay's text back into rows is fragile and drops slots.**
  The human text has no slot numbers; reconstructing them is a correctness risk.
  Recommend a small structured output mode on `expand_replay` instead of a
  reverse-parser.
- **We're building against an unshipped runtime with no input-wiring story.**
  We can conform to messages but can't integration-test, and we must invent how
  the replay URI arrives. Fine to proceed, but the wiring is provisional and
  should be labeled as such.
- **"Minimal" still means a real WebSocket service.** The protocol loop,
  healthz, drain, and (if parquet) the binary-frame discipline are irreducible.
  The wrapper is small, but it isn't trivial.

---

## 7. Recommended plan

1. **Confirm §3 decisions** (output = JSON; expand_replay gets a structured mode;
   language = Nim) and the §5 open questions.
2. Add `--format ndjson` (structured rows) to `tools/expand_replay.nim`; keep
   text output byte-identical; lock both with a test.
3. Build `reporters/eventlog/`: `protocol.nim` (+ tests), `report.nim` (rows →
   JSON), `service.nim` (mummy WS loop), `Dockerfile`, `README.md`.
4. Test: protocol round-trip via a mock WS client; extraction against
   `tests/replays/notsus.bitreplay`.
5. Add the manifest entry in the new shape **only when** the runtime is ready to
   invoke it (per §5.5), or land it behind a clear "declared, not yet invoked"
   note.

If instead you want the **literal** prompt (frozen expand_replay, parse text,
parquet out), it's buildable — but it's the larger, more fragile, less minimal
of the two paths, and I'd want sign-off on the slot-reconstruction and parquet
costs first.
