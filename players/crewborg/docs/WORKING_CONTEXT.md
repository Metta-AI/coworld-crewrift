# crewborg working context

**What this is.** The live, high-signal state of *what we're working on right now* with crewborg —
the minimal set of cross-session facts worth carrying into the next session. Read it on startup to
resume; **update it as you learn** (keep it tight — prune anything no longer load-bearing). **Clear
and reseed it when we pivot** to a whole new direction, keeping only the new objective.

This is *not* a log or archive: finished work lives in git history / the
[version log](../crewborg/version_log.md); durable disciplines live in
[`best_practices.md`](best_practices.md); how the agent works lives in the
[package docs](../crewborg/docs/README.md). This file is the one-screen "where are we."

> **This is a seed.** It was distilled when crewborg was packaged into `coworld-crewrift`; the IDs,
> warehouses, and exact numbers below are a starting point, not gospel. Re-derive against the live
> league before relying on any figure, and reseed this file as you work.

---

## Current state

**crewborg is a deterministic-first policy with optional, gated LLM layers** (meeting chat/vote and
a gameplay commander), both off unless enabled at upload. The deterministic path is the proven
champion; the LLM layers are built but not a confirmed win in a competitive field.

- **The deterministic imposter combo is the confirmed shippable gain**: *drop the unwitnessed
  requirement after the first kill* (Hunt strikes a witnessed victim once a kill is banked) + a
  post-kill Evade re-approach. Measured vs an older self in natural roles: ~+19pp ≥2-kill, ~+14pp
  win, +0.32 kills (p≈0.04). See [`../crewborg/docs/imposter-play.md`](../crewborg/docs/imposter-play.md).
- **The meeting LLM** (`CREWBORG_LLM_MEETINGS=1` + Bedrock) is verified firing in experience-request
  (k8s) pods, but has historically **fallen back to deterministic in league/dispatch rounds** (the
  Bedrock sidecar is wired for xreq jobs, not league pods). So an LLM-on build may just add startup
  weight + silent fallback in league. **Open: confirm whether the meeting LLM fires in league rounds.**
- **The gameplay commander** (`CREWBORG_LLM_COMMANDER=1`) is built end-to-end and its *control
  capacity is demonstrated* (a forced run provably steers both roles), but it is **not performance-
  tuned** — early use will often degrade play. See [`../crewborg/docs/commander.md`](../crewborg/docs/commander.md).

## 🎯 The headline lever: imposter kill → WIN conversion (not kill count)

In a champion field, crewborg's **kills are competitive** but its **imposter win rate lags** — it
gets the kills, then loses the game more often than the top imposters do on the *same* kill count.
The frontier is **converting kills into wins**: surviving the meeting, reaching parity, not getting
voted out for witnessed kills (the witness-drop's likely ejection backlash against competent crew).

**Open diagnosis (start here):** pull the games where crewborg out-kills but LOSES, and separate
(a) **ejection** (witnessed-kill backlash → voted out), (b) **failing to reach parity** (kills too
slow/late), (c) **meeting/survival**. That diagnosis sets the next fix.

## Loose threads

- **Post-kill re-approach (not solved).** After a kill, crewborg drifts away from crew before the
  next cooldown clears, while top imposters stay glued and snowball. The lever: re-establish contact
  with the **single nearest ISOLATED victim** (not the densest crowd — crowd-seeking is a confirmed
  dead end; crowds are witnesses), **sustained across the whole cooldown** (the long random-Search
  window is a bigger culprit than the short Evade window). Forks: a dedicated re-approach state
  spanning Evade→Search; or strengthen Recon (longer window, head to a live/predicted single victim).
- **Crew side.** crewborg is consistently the best *tasker* but crew win rates are low across the
  field (imposter-dominated). A raised direction: **punish aggressive imposters** (detect relentless
  proximity/kills) to cut the opponents' imposter win and lift our crew win.
- **Commander tuning.** If pursued: A/B the imposter commander (LLM on vs off) for kill efficiency
  and iterate the imposter prompt to emit useful `hunt_room` / `target_player` / `strength`.
- **Suspicion weights** are refit periodically from scraped league replays (the fitting pipeline is
  in the optimizer toolkit, not the package). See [`../crewborg/docs/suspicion.md`](../crewborg/docs/suspicion.md).

## Known small debts (from packaging)

- The bridge's reconnect give-up path logs `NoneType` for the cause when every attempt was 0-frame
  (cosmetic).
- `FLEE_PROBABILITY` is a legacy constant name (the Flee mode is retired → Accuse); `believed_imposters`
  is now a traced/serialized readout, not a mode gate.
- The in-package `crewborg/tools/` (path-prediction analysis) and `crewborg/viewer/` are relocated but
  not yet refined.
