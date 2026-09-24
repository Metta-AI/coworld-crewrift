# crewborg working context

**What this is.** The live, high-signal state of *what we're working on right now* with crewborg —
the minimal set of cross-session facts worth carrying into the next session. Read it on startup to
resume; **update it as you learn** (keep it tight — prune anything no longer load-bearing). **Clear
and reseed it when we pivot** to a whole new direction, keeping only the new objective.

This is *not* a log or archive: finished work lives in git history / the

---

## 🎯 Current state (seeded at the 2026-07-01 sync — the v82 code line)

**This package now carries the code that is Crewrift Prime CHAMPION as `crewborg:v82`** (2026-07-01):
- **Imposter idle-freeze fixes** — RECON never idles/stalls (abandons reached-stale targets, falls back
  to expected-crew seek; recon gated to the pre-kill-ready window); SEARCH is a 5-state FSM
  (PICK_ROOM/GO_TO_ROOM/SEARCH_ROOM/WATCH/FOLLOW) with a scored, env-tunable PICK_ROOM
  (`CREWBORG_PICKROOM_W_*`). Measured: idle-while-ready 0.68 → 0.10, freezes ≥1k ticks 23 → 1,
  kills 1.18 → 1.91/game across the fix line.
- **Role-latch fix** — role latches from the RoleReveal TEXT (`IMPS`/`CREWMATE`), never from reveal
  icons (crew reveals also render the 9500+ icon range; the icon latch made crew play as imposters —
  0 tasks, silent skip-votes, no chat). If you fork this code, do not widen that latch.
- League telemetry: upload with `CREWBORG_METRICS=1 CREWBORG_TRACE_GROUPS=all` (see
  user_preferences.md); league artifacts are EPHEMERAL (~one round) — harvest promptly.

## ▶ Open threads (2026-07-01)

September 24 Jev pilot: draft PR #175 adds a Jev System One meeting-vote client and reads the vote timer from the Game Info screen. The old 240-tick assumption did not match Classic (7,200), Prime (1,200), or the vote drill (600). The local `linux/amd64` image completed role-aware crew and imposter episodes. Crew made six Jev calls for $0.001350216 and cast two skip votes; imposter made four calls for $0.000998004 and cast votes for blue and skip. Both seats had zero vote timeouts. These are liveness checks, not gameplay comparisons. `startWaitTicks=0` suppresses Game Info in the current game version, so the timer cannot be read in that variant; the live Classic and Prime configurations show it. Hosted trials are recorded below; no policy was submitted to the league.

September 24 replay follow-up: three source-built local games produced hash-checked replays and twelve typed Jev meeting choices. The exporter joined four choices to applied votes and left eight unselected. Metta imported two accepted training labels and two seed-separated validation labels; a one-step 8,192-token adapter lowered completion loss on both heldout CrewRift decisions. This is an offline data-path check. Hosted sidecar proof is recorded below; a matched gameplay comparison remains open.

September 24 hosted sidecar follow-up: a CrewRift Prime Experience Request (`xreq_92c82a31-6d5d-4cdc-a7ad-3d06a6b551ad`) completed with three Jev `typed_choice` decisions and no model fallback. A first language trial (`xreq_6436d4ca-1563-4217-92d4-ee7a75b9fb0e`) exposed an `InvokeModel` routing error. The corrected Messages client completed `xreq_4dd03a6a-bc1d-42bf-8838-c204fd93c77e` with 17 `native_language` decisions, 16 sent chats, and two cast votes. Two late calls hit the trial's $0.125 per-seat spend ceiling and fell back. The hosted proof uses a lean trial image without optional spaCy chat parsing; the matched gameplay comparison remains open.

September 24 hosted replay join: both completed trial replays passed version-matched hash checks and matched all official seat outcomes. The exporter now compares replayed chat against CrewRift's sanitized model text. It joined one Jev vote and three language chats plus one language vote to selected model attempts; the other 15 attempts remain unselected evidence. Metta accepted both strict `CompleteEpisode` records into a private 31-episode corpus across 11 games: 288 training and 316 validation labels. All 604 labels fit 8,192 Qwen2.5 tokens. A one-step CPU adapter trained on the expanded corpus, and its exact-training-hash-bound heldout loss check improved on two examples per game across all ten validation games. This is data-path evidence, not a gameplay gain.

September 24 relh production canaries: the production Dockerfile produced a `linux/amd64` image and relh-owned `crewrift-jev-meetings-20260924:v1` and `:v2`. Private Classic and Prime Experience Requests used active opponents, a $0.05 combined player model cap each, and no ladder submission. Both v1 seat-0 crew episodes scored 1; read-only usage records show Prime made two model calls for $0.000544 with zero cap rejections. V2 sent all telemetry to stderr because Experience Requests did not return v1's artifact zip. Prime v2 scored 1 as crew; Classic v2 scored 3 as imposter. Neither timed out. The 10,000-line policy-log cap retained only the final 443 Prime ticks and 368 Classic ticks, so those logs do not prove meeting calls or fallback counts. V1 Prime proves hosted Jev routing; the four single episodes do not estimate a win rate.

1. **Crew vote rate is evidence-limited, not gate-limited.** Crew votes only at fitted P≥0.9
   (`CREWBORG_WEIGHTS_VOTE_P`, `strategy/suspicion.py`); live posteriors cross it in only ~23% of
   meetings (median max-posterior at meeting ≈ 0.67) since the game's 0.4.28/29 update. Precision is
   the best in the field (67% vote-hit-imposter) but volume is ~1/3 of top rivals. The lever is
   warming evidence accumulation, not lowering the threshold (0.8 is the only defensible sweep value).
2. **Meeting timing** — PR #175 reads the game's `VOTE TIMER` value when Game Info is shown. The `startWaitTicks=0` variant suppresses Game Info and still needs a source-of-truth timing path.
3. **Slot-4 role-limbo**: a crew seat at slot 4 can miss the CREWMATE reveal text entirely →
   `self_role=None` forever → frozen, 0 task attempts (~15% of crew games). Needs a bounded
   fallback-to-crew escape in `types.py` (keep the positive latch as primary).
4. **Imposter 2nd-kill conversion**: sits kill-ready with a target visible ~43% of ready ticks
   (4× rivals) yet converts no faster — the long-standing hesitancy lever.
