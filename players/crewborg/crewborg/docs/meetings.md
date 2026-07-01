# Meetings: chat and vote subsystem

The cross-cutting reference for everything crewborg does during a Crewrift **meeting** —
deciding what to say in meeting chat and who to vote out. It covers both paths the
subsystem runs: an always-present **deterministic** path and an opt-in **LLM** path.

This is a deep reference. For orientation start with [`../README.md`](../README.md); for the
architecture see [`../design.md`](../design.md). Adjacent references:
[crewmate play](./crewmate-play.md), [imposter play](./imposter-play.md),
[the suspicion model](./suspicion.md), [perception and belief](./perception-and-belief.md),
[the gameplay commander](./commander.md), and [trace logs](./trace-logs.md).

Scope boundaries (deferred, not duplicated):

- The suspicion **model** that ranks suspects and produces `top_suspect` →
  [`./suspicion.md`](./suspicion.md). This doc consumes its output.
- The role-level decision of **when** to accuse versus skip →
  [`./crewmate-play.md`](./crewmate-play.md) / [`./imposter-play.md`](./imposter-play.md).
- The separate in-game **commander** LLM (movement/strategy, not meetings) →
  [`./commander.md`](./commander.md). Unrelated to the meeting LLM described here.

---

## 1. Where it runs

The meeting subsystem owns the entire **Voting** phase. One mode drives it:
`modes/attend_meeting.py:AttendMeetingMode`, selected by the rule layer for the whole
phase (`AttendMeetingMode.is_legal` returns true exactly when `belief.phase == "Voting"`).
The mode emits only symbolic `chat` / `vote` / `idle` intents; walking to the vote panel
and pressing keys is `action.py`'s job.

The decision pieces live in `strategy/meeting/`:

| File | Responsibility |
| --- | --- |
| `context.py` | Serialize `Belief` into the compact JSON the LLM reasons over; **owns vote legality** (`valid_vote_targets`). |
| `schema.py` | The typed `MeetingDecision` the LLM answers in, plus the sanitize/validate gate. |
| `llm.py` | Client/backend selection (direct Anthropic vs Bedrock sidecar); the call adapter. |
| `prompts.py` | Role-specific system-prompt assembly (common contract + crewmate/imposter doctrine). |
| `memory/crewmate.md`, `memory/imposter.md` | Editable role doctrine text loaded by `prompts.py`. |
| `accusation.py` | Render a crewmate accusation, or an imposter's *fabricated* one, through one shared template. |
| `imposter.py` | Pick which crewmate to bandwagon onto (heat from votes + chat). |
| `chat_read.py` | Read opponents' chat for *who is being accused* (feeds the bandwagon). |
| `chat_nlp.py` | spaCy lifecycle (background load, kill switch) behind `chat_read`. |

`strategy/meeting/__init__.py` is the package facade re-exporting the names
`attend_meeting.py` imports.

---

## 2. The two paths

```
                       Voting phase tick
                              |
                  AttendMeetingMode.decide
                              |
        vote already confirmed / committed? --yes--> re-emit / idle
                              | no
              LLM client enabled (opt-in)? 
                  /                       \
                no                        yes
                 |                          |
        DETERMINISTIC path          past auto-submit deadline? --yes--> force vote
        (always present)                    | no
        role-specific rules         cadence trigger this tick?
                 |                          | yes
        legal vote before timer      call LLM -> validate decision
                                            |        (fail/invalid)
                                     apply decision  -----> deterministic fallback
```

The split is the whole point of the layer. The deterministic path is the floor: it always
produces a legal chat/vote with no model call. The LLM path is strictly **opt-in**
(`CREWBORG_LLM_MEETINGS=1`) and strictly **best-effort** — when it is off, fails to build,
times out, errors, or returns an illegal decision, the mode falls through to the
deterministic path or forces the staged vote. The LLM is never on the critical path for
*landing a legal vote*.

### The deadline-safety invariant

Missing a vote costs points, so the subsystem guarantees a legal vote is cast before the
timer expires regardless of path. `attend_meeting.py:_should_auto_submit` returns true once
the meeting clock is within `AUTO_SUBMIT_REMAINING_TICKS` (48) of expiry and we have not yet
voted; at that point `decide` force-submits the staged vote. An LLM call is only *started*
if it can finish (timeout + margin) before that window — see §9.

The two structural guarantees that make this hold:

1. `llm.py:build_meeting_llm_client_from_env` is **total** — it returns a
   `DisabledMeetingClient` rather than raising, so the fallback path is always reachable.
2. `schema.py:validate_meeting_decision` is a **trust boundary** — it raises on anything
   illegal, and the caller treats a raise as "use the deterministic vote".

---

## 3. Vote legality (single source of truth)

`context.py:valid_vote_targets(belief)` is the one definition of "who may be voted for". It
returns the live, votable player colors **excluding self** — the meeting candidate set if
present, otherwise the alive roster. `VOTE_SKIP` (`"skip"`) is always additionally legal.

Three call sites share it, so the legality the prompt advertises is exactly the legality the
validator enforces and the mode resolves against:

- `context.serialize_meeting_context` lists `[*legal_targets, VOTE_SKIP]` under
  `constraints.valid_vote_targets` in the prompt context.
- `schema.validate_meeting_decision` checks the model's `vote_target` against it.
- `attend_meeting._resolved_vote_target` uses it to decide whether a staged tentative vote
  is still castable.

On top of that, `attend_meeting._submit_vote_intent` applies a **hard self-vote guard**:
whatever the resolved target, if it equals our own color
(`belief.self_color or belief.voting.self_marker_color`) it is coerced to `VOTE_SKIP`. The
agent can never vote itself out.

---

## 4. Deterministic path

`attend_meeting._decide_deterministic` branches on role. Both roles **couple chat to the
vote** — we accuse exactly whom we vote, never one without the other — and both always reach
a legal `submit_vote` before the timer. Chat and vote are never default-fired filler.

### Crewmate (`_decide_crewmate`)

Restraint by design. On the first deterministic tick it reads `suspicion.top_suspect(belief)`
— the clear leading suspect at or above the vote bar (`suspicion.VOTE_PROBABILITY`, currently
`0.8`), or `None` on a flat field:

- **Clear suspect** → stage the vote on that suspect and, if `accusation.build_accusation`
  produces a citable line, send it (path `accuse`); if there is no citable evidence, vote
  silently (path `vote_no_chat`).
- **Flat field** → stay silent and skip (path `silent_skip`).

It then submits the staged vote. Loose accusing is negative-EV for crew, so silence + skip is
the default, not a failure mode. The *when-to-accuse* policy lives in
[`./crewmate-play.md`](./crewmate-play.md); the ranking lives in [`./suspicion.md`](./suspicion.md).

### Imposter (`_decide_imposter`)

Deflect heat onto crewmates, never teammates, and survive the meeting. Order of preference:

1. **Proactive deflection** (path `proactive`) — if `top_suspect` names a non-teammate with
   real citable evidence, accuse and vote them with `build_accusation`.
2. **Reactive bandwagon** (path `bandwagon`) — otherwise pile onto a crewmate already taking
   heat. `imposter.bandwagon_target` scores heat as
   `votes·VOTE_WEIGHT(2) + distinct chat-accusers·CHAT_WEIGHT(1)`, excluding teammates, self,
   the dead, and skip; the most-heated crewmate wins. The accusation is **fabricated**
   (`accusation.fabricate_accusation`) but rendered in the identical format (§6).
3. **Parity-closing push** (path `parity_push`) — if the board is **one removal from parity**
   (`crew_alive − imp_alive == 1`) and a **live teammate is known** (`alive_imposter_count >= 2`),
   *manufacture* a vote: `imposter.parity_closing_vote_target` picks the best non-teammate crewmate
   (shared deterministic rank — existing votes, then lowest slot — so both imposters stack the same
   target into a plurality), and we accuse+vote it like a bandwagon. Both gates keep it from the
   "vote aggression raises ejection" trap: it only fires when the parity math/teammate exclusion are
   trustworthy and a single ejection *wins the game*. **A/B-validated: imposter win +14.4pp (p<1e-9),
   kills flat.** Rationale, evidence, and merge guide: the lab design doc `imposter-parity-meeting.md` (not shipped in this package).
4. **Skip** (path `skip`) — if nobody is taking heat and we're not parity-closing, idle and watch; skip at the deadline.

`imposter.votes_against` counts votes cast against each candidate by *other* players (skip
votes and our own ballot excluded) and is also surfaced in the trace. The **never out a
teammate** invariant is enforced structurally in `bandwagon_target`'s exclusions and in
`top_suspect` (which the suspicion model keeps off teammates). See
[`./imposter-play.md`](./imposter-play.md) for the role strategy.

---

## 5. Reading opponents' chat

The imposter bandwagon needs to know which crewmates *others* are sussing in chat, before it
hardens into a vote. `chat_read.py:chat_accusers` returns, per non-teammate color, the count
of distinct other speakers who accused them. It runs in two stages:

1. **Keyword pre-gate** (`_gate`) — a message is only worth parsing if it both names a color
   (the closed roster set) and carries a `SUS_WORDS` cue. Most chatter is filtered here.
2. **Dependency-parse negation scope** (`_extract`, via spaCy) — the real value. For each
   color token it gathers the governing clause via the head-chain and marks the color
   *accused* only if that clause carries a sus cue, the color is not an adjacent victim
   (`VICTIM_WORDS`), and the clause is not negated or defended (`_negated`, checking
   dependency `neg` children, `NEG_WORDS` governed by the chain, and `DEFENSE_WORDS`). This
   distinguishes `"red isn't sus"` from `"red is sus not blue"`, handles the victim flip
   (`"when red died"` ⇒ red is the victim), and respects defenses.

Own chat is filtered out (not a signal to bandwagon on), and teammates/self are excluded from
the result. Parses are memoized per message text by a caller-owned cache reset each meeting
(`attend_meeting._chat_parse_cache`).

### spaCy lifecycle (`chat_nlp.py`)

Loading `en_core_web_sm` costs ~0.5 s on a full core and ~1.5–2 s under the hosted ¼-core
cap, so it is **never** loaded on the gameplay hot path:

- `ensure_loading()` starts a single background daemon thread (idempotent), kicked off at
  agent build (`crewborg.__init__.build_runtime`) so the load overlaps pre-game idle.
- `get_model()` returns the pipeline only once ready, and `None` until then.
- Every caller degrades gracefully: when the model is disabled / still loading / failed,
  `chat_accusers` returns `{}` and the bandwagon rests on the reliable vote tally alone. The
  layer deliberately does **not** fall back to crude keyword matching — its false positives
  are exactly what the dependency parse exists to avoid.

The whole feature is gated by `CREWBORG_CHAT_NLP` (default **on**); set it to
`0`/`false`/`no`/`off` and spaCy is never imported and chat parsing is off. `chat_nlp.state()`
reports `disabled` / `loading` / `ready` / `failed` for tracing. NER is disabled at load
(only the dependency parse + its tagger are needed).

The LLM path does not call `chat_read`: it receives the raw chat log directly in its context
(§7) and reasons over it itself.

---

## 6. The anti-tell: identical chat format

Crew and imposter meeting chat use the **identical** shape, so an observer cannot infer role
from chat structure. `accusation.py:_format` renders both a real crewmate accusation
(`build_accusation`) and a fabricated imposter one (`fabricate_accusation`) through the same
template:

```
<color> sus: <reason 1>, <reason 2>          (top MAX_REASONS = 3, truncated to CHAT_MAX_CHARS)
```

`build_accusation` ranks the suspect's *real* evidence by each cue's log-likelihood-ratio
(reusing the per-event scorers from `suspicion.py`) and maps the winning cues to phrases.
`fabricate_accusation` cites only **safe, hard-to-disprove** cues — proximity to a real
body, tailing, vent lingering — and never a bold, falsifiable claim (a witnessed kill/vent)
another player could contradict. Only the *evidence* differs (real vs invented); the wording
and structure are the same, so the accusation is not a role tell.

---

## 7. LLM path: enablement and backend selection

The LLM path is opt-in. `llm.py:build_meeting_llm_client_from_env` reads the environment and
returns a client implementing the `MeetingLLMClient` protocol. It **never raises** — any
failure (flag off, no backend, SDK import error, bad config) becomes a `DisabledMeetingClient`
carrying a `disabled_reason`, and the caller takes the deterministic path.

Gating order:

```
CREWBORG_LLM_MEETINGS truthy?  --no-->  DisabledMeetingClient("CREWBORG_LLM_MEETINGS not enabled")
        | yes
load player SDK helpers (lazy import)
        |
use_bedrock = bedrock_enabled(env)  OR  sidecar endpoint present
        |
neither Bedrock nor ANTHROPIC_API_KEY?  --yes-->  DisabledMeetingClient("no LLM backend configured")
        | no
build MeetingLLMConfig + select_client  ->  AnthropicMeetingClient
        |
any exception anywhere above  ----------->  DisabledMeetingClient("construction failed: ...")
```

`CREWBORG_LLM_MEETINGS` accepts `1`/`true`/`yes`/`on`.

### Bedrock sidecar gating (load-bearing)

The hosted runner strips `USE_BEDROCK` from the player container and instead injects a
loopback proxy endpoint, `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`. The SDK's own
`bedrock_enabled(env)` only checks `USE_BEDROCK`/`CLAUDE_CODE_USE_BEDROCK`, so it reports *no
backend* in-pod. `llm._sidecar_bedrock` treats the presence of that endpoint as the real
Bedrock signal, so `use_bedrock = bedrock_enabled(env) or _sidecar_bedrock(env)`. Without this
gate, hosted Bedrock games silently run LLM-off.

### Client construction

`build_meeting_llm_client_from_env` resolves the model and timeout from env (§10), calls the
SDK's `select_client(use_bedrock=..., timeout=...)`, and wraps it in `AnthropicMeetingClient`
along with the SDK's `call_json` and `extract_json_object`. The model id is chosen by the
SDK's `resolve_model` — the Bedrock inference-profile id when on Bedrock, the direct id
otherwise, or an explicit `CREWBORG_LLM_MODEL` override. The SDK is **lazily imported** inside
`_load_sdk_helpers` so importing this module never hard-depends on the SDK; a missing SDK
degrades to a disabled client.

### One call (`AnthropicMeetingClient.decide`)

The user message is the serialized context plus a compact restatement of the expected
response schema, as JSON. The system prompt is role-selected (§8). The model's text is parsed
with `extract_json_object` and validated into a `MeetingDecision`; a parse failure raises and
the caller falls back. The result carries the decision plus call metadata
(`model`, `latency_ms`, `usage`, and raw request/response when `trace_raw` is on).

### Context serialization (`context.py:serialize_meeting_context`)

One pre-digested, side-effect-free projection of belief per LLM tick. It spells out, so the
model reasons over already-computed signals rather than re-deriving them:

- `meeting` — id, tick, age, estimated remaining ticks (`VOTE_TIMER_TICKS = 240`).
- `self` — our color, role, and teammate colors.
- `constraints` — the action menu, `valid_vote_targets` (§3), `CHAT_MAX_CHARS`, printable-ASCII
  requirement, and chat-cooldown readiness (`CHAT_COOLDOWN_TICKS = 100`).
- `state` — the staged tentative vote, the deterministic `fallback_vote`, and a human reason
  for it (skip vs named suspect + its P(imposter)).
- `voting` — live ballot: cursor, candidate rows (self/teammate/suspicion flags), cast votes,
  and a target→count tally (raw skip markers mapped to `VOTE_SKIP`).
- `chat` — the meeting chat log, each line flagged `self` if we said it.
- `players` — per-color records: life status, last-seen position/age, death/body info,
  suspicion, confirmed/believed-imposter flags, and the last 8 behavior events.
- `suspicion` — the model's summary: prior, vote bar, confirmed/believed sets, the full
  descending P(imposter) ranking, and the deterministic `would_vote`.

Every value the model might emit (a vote target, a skip token) has a legal example present in
the context.

---

## 8. Prompts and role doctrine (`prompts.py`)

The system prompt is two parts joined by `system_prompt_for_context`:

- A role-independent **common contract** (`_COMMON_PROMPT`) — the four actions
  (`send_chat` / `set_tentative_vote` / `submit_vote` / `wait`) and the hard rules: use only
  `valid_vote_targets` or `skip`, keep chat printable ASCII ≤ `CHAT_MAX_CHARS`, a submitted
  vote is final, prefer game-grounded speech. The stated limits mirror what
  `schema.validate_meeting_decision` enforces.
- A **role doctrine** selected from `context["self"]["role"]` — `"imposter"` loads the
  imposter doctrine; anything else (including a missing role) maps to crewmate, the safe
  default.

Doctrine text is loaded from editable markdown — `memory/crewmate.md` and
`memory/imposter.md` — so voice and strategy can be tuned without a code edit. The override
directory is `CREWBORG_LLM_PROMPT_DIR`; loaded prompts are cached by `(role, dir)`
(`lru_cache`). If a file is missing/unreadable/empty, a baked-in `_FALLBACK_ROLE_PROMPTS`
string is used, so a call always has a usable prompt.

Both doctrines encode the same invariants the deterministic path obeys — crewmate restraint
(skip unless concrete, citable evidence; never invent cues; defend yourself when wrongly
accused), and imposter discipline (never out a teammate; deflect onto a plausible
non-teammate; bandwagon with safe fabricated cues; skip when no deflection is safe). The
prompt shapes *content and strategy only*; the chat **format** is not role-specific here,
consistent with the anti-tell (§6).

---

## 9. Decision schema, validation, and cadence

### Schema (`schema.py`)

`MeetingDecision` is a pydantic model with `extra="forbid"` (unknown keys fail validation):
`action` (the four `MeetingAction`s), optional `chat_text`, optional `vote_target` (a color,
`VOTE_SKIP`, or `None`), a free-text `reason` (traced, not acted on), and an optional 0.0–1.0
`confidence`. `SCHEMA_VERSION = 1` is pinned via `Literal[1]`.

`validate_meeting_decision` is the gate every decision passes. It returns a **new**
sanitized/validated decision or raises `MeetingDecisionValidationError`:

- `chat_text` is sanitized to printable ASCII ≤ `CHAT_MAX_CHARS` (`sanitize_chat`).
- `vote_target` is lower-cased/stripped (`normalize_vote_target`) and checked against
  `valid_vote_targets ∪ {skip}`.
- A `submit_vote` with no explicit target resolves to the tentative, else the fallback, else
  `VOTE_SKIP` — a submit always yields a legal ballot.
- `set_tentative_vote` must name a target; `send_chat` must carry non-empty printable text.

There is no confidence-gated vote downgrade — the LLM owns the vote, bounded only by legality
and the self-vote guard.

### Call cadence (`attend_meeting._next_llm_trigger`)

The LLM is not called every tick. A call fires only on a trigger, gated by the minimum call
interval (`LLM_MIN_CALL_INTERVAL_TICKS = 12`) and the latest-safe-start deadline. Triggers, in
priority order:

| Trigger | Fires when |
| --- | --- |
| `meeting_start` | The first call of the meeting. |
| `deadline` | Remaining ticks ≤ the deadline-prompt threshold; a one-shot final prompt. |
| `new_chat` | The external-chat signature changed since the last call (genuinely new chat). |
| `chat_cooldown_ready` | Our own chat cooldown just cleared and we haven't already prompted on it. |

`_call_llm` snapshots bookkeeping *before* the call (so a failure still advances cadence),
latches the one-shot deadline prompt, and emits the `meeting_llm.latency_ms` histogram.

### Decision application (`_apply_decision`)

A validated decision updates the tentative vote (if it names one), then dispatches on action:
`send_chat` emits chat now if the cooldown is ready and it isn't a duplicate (else stashes it
as `_pending_chat_text` to flush when the cooldown clears); `submit_vote` commits and emits
the vote; `set_tentative_vote` / `wait` idle and keep deliberating. Duplicate chat is
suppressed against `_sent_chat_texts`.

### Latency and deadline math

The call is a synchronous blocking `call_json` on the meeting fast path. Because meetings
freeze movement/combat, a bounded blocking call is acceptable, but a slow/failed call must
never miss the vote. The guards:

- `_latest_safe_llm_start_remaining_ticks` =
  `AUTO_SUBMIT_REMAINING_TICKS (48) + ceil(timeout_s · MEETING_TICKS_PER_SECOND (24)) + LLM_TIMEOUT_MARGIN_TICKS (12)`.
  With the default 3.0 s timeout this is `48 + 72 + 12 = 132` ticks.
- `_can_start_llm_call` refuses to start a call once remaining ticks drop to/below that floor.
- The `deadline` prompt threshold is `max(DEADLINE_LLM_REMAINING_TICKS (96), latest_safe + 1)`,
  so the final prompt is never scheduled too late to finish.
- `_should_auto_submit` force-submits the staged vote at `AUTO_SUBMIT_REMAINING_TICKS` (48)
  regardless of LLM state.
- `_decide_after_llm_failure`: at the `deadline` trigger a failure force-submits; at
  `meeting_start` it falls through to the deterministic path; otherwise it idles and waits for
  the next trigger.

So the worst case — the LLM hangs to its full timeout right at the latest safe start — still
returns with margin before auto-submit, and a hard failure still lands a legal deterministic
vote.

---

## 10. Configuration

All meeting LLM knobs are env-driven and read in `build_meeting_llm_client_from_env`
(`MeetingLLMConfig` carries the resolved values).

| Env var | Default | Effect |
| --- | --- | --- |
| `CREWBORG_LLM_MEETINGS` | off | Master opt-in for the LLM path. `1`/`true`/`yes`/`on` enables it. |
| `CREWBORG_LLM_MODEL` | SDK-resolved | Explicit model id override (else Bedrock/direct id per backend). |
| `CREWBORG_LLM_MAX_TOKENS` | 512 | Generation cap. |
| `CREWBORG_LLM_TEMPERATURE` | 0.2 | Low, for steadier meeting behavior. |
| `CREWBORG_LLM_TIMEOUT_SECONDS` | 3.0 | Per-call wall-clock budget; also feeds the latest-safe-start math. |
| `CREWBORG_LLM_PROMPT_DIR` | `memory/` | Override directory for role prompt files. |
| `CREWBORG_LLM_TRACE_RAW` | off | Include raw request/response in the result for `meeting_llm_debug`. |
| `CREWBORG_TRACE` | — | `debug` also turns on raw tracing. |
| `CREWBORG_CHAT_NLP` | on | Kill switch for spaCy chat parsing (deterministic imposter bandwagon). |
| `ANTHROPIC_API_KEY` | — | Direct Anthropic backend (the non-Bedrock path). |
| `USE_BEDROCK` / `CLAUDE_CODE_USE_BEDROCK` | — | Bedrock backend (set by `--use-bedrock` at upload). |
| `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` | — | Sidecar Bedrock signal injected by the hosted runner. |

The default Bedrock model id resolves through the SDK; the dataclass fallback default is
`claude-haiku-4-5-20251001`.

---

## 11. Tracing

The mode emits a rich set of `meeting_*` events and counters; see
[`./trace-logs.md`](./trace-logs.md) for the trace catalog. The load-bearing ones:

| Event | Meaning |
| --- | --- |
| `meeting_context_serialized` | The full context shipped to the LLM (trigger + context). |
| `meeting_llm_decision` | The validated LLM decision (trigger, model, latency, usage). |
| `meeting_llm_debug` | Raw request/response, only with raw tracing on. |
| `meeting_llm_fallback` | Why the LLM path yielded — disabled, call failed, invalid decision, duplicate/cooldown chat. |
| `meeting_decision` | The deterministic decision: role, path, target, real-vs-fabricated, heat (imposter), NLP state. |
| `meeting_tentative_vote` / `meeting_vote_selected` / `meeting_chat_selected` | The staged vote, committed vote, and emitted chat. |
| `meeting_llm.latency_ms` (histogram) | Per-call latency by model and trigger. |

---

## 12. Invariants to preserve

- **The deterministic fallback is never bypassed.** The LLM is opt-in and best-effort; a
  legal vote always lands before the timer. (`build_meeting_llm_client_from_env` returns a
  disabled client rather than raising; `validate_meeting_decision` rejects illegal output;
  `_should_auto_submit` force-submits at the deadline.)
- **`valid_vote_targets` is the one definition of vote legality** — prompt, validator, and
  resolver all call it, so the legality advertised is the legality enforced.
- **Never vote ourselves out** — the hard self-vote guard in `_submit_vote_intent`.
- **Never out a teammate** — the deterministic imposter excludes teammates structurally
  (`bandwagon_target`, `top_suspect`); the imposter prompt forbids it.
- **Chat is not a role tell** — real and fabricated accusations render through the identical
  `accusation._format` template.
</content>
</invoke>
