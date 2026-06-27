---
name: coworld-policy-lifecycle
description: "Use to submit an ALREADY-UPLOADED policy version to a Coworld league and monitor whether it QUALIFIES, then competes (and becomes champion). Triggers: 'submit crewborg to the league', 'did it qualify', 'is it disqualified / champion', 'watch the qualifier', 'monitor standings after submitting'. SUBMIT is the public, effectively-irreversible, champion-making action — explicit human go-ahead only. (Building + uploading a version is the separate `build & upload` skill.)"
---

# Coworld Policy Lifecycle — Submit & Monitor

This skill owns the **rare, high-stakes** end of the loop: take a version you've *already built,
uploaded, and proven*, **submit** it to a live league, and **monitor whether it actually
QUALIFIES** — then competes and (optionally) becomes champion. It assumes the version exists; the
routine **build + upload** every iteration is the separate **`build & upload`** skill.

> **Upload freely; submit rarely.** Uploading a version is routine and inert. **`submit` is the
> public, gated, effectively-irreversible action** — it enters a version into a live league where it
> can become **champion** once it qualifies. Only submit a **demonstrably-better** player with
> **explicit human go-ahead**. *Not* submitting is your rollback.

**Announce at start:** "This submits `<name>:vN` to the live league (Gate 2) — confirming go-ahead
first. Then I'll background a qualification monitor so we can keep working while it qualifies."

## Prerequisite — an uploaded, eval-proven version

Before submitting you should have: a version **built + uploaded** (`build & upload` skill), passing a
**Gate-1 smoke** (`coworld-local-run`), and shown **better than the incumbent via experience
requests** (`coworld-experience-requests`). To pick which `vN` to submit, list uploads and consult
the version log via the **`build & upload`** skill (`scripts/versions.py --name crewborg` +
[`version_log.md`](../../crewborg/version_log.md)).

## Step 1 — Submit (gated; confirm go-ahead first)

```bash
uv run coworld submit crewborg:vN --league <league_id> [--auto-champion always|never|lineage]
```

- **Re-resolve `<league_id>` live** (`coworld leagues`); ids rotate.
- **You do NOT pick a division.** `submit` sends only `{league_id, policy_version_id}`; the **server**
  resolves the target division: the league's **Qualifiers** (staging) division if its config names a
  live one → the membership starts **`qualifying`**; otherwise the **Competition** entry division —
  which, for Crewrift's container-commissioner league, **also starts `qualifying`** (event-driven).
  *(The old "you must place it into a qualifier division" model is wrong — verified in metta
  `pipeline.py` + `division_selectors.py`; see `references/cli.md`.)*
- **`--auto-champion`** governs promotion **after** it qualifies: `always` (promote whenever it
  qualifies — default) · `never` (place but never auto-champion) · `lineage` (only replace *your own*
  prior champion).
- **If it's `rejected`** (the monitor surfaces `notes`): `"… already has an active membership in this
  league"` (dedup — retire the old membership or submit a *new* version) · `"league has no
  divisions"` · `"league has no submission division"` (now **rare** — a genuine **league
  misconfiguration**; escalate to the league owner / commissioner, it is **not** a division you
  choose).

## Step 2 — Monitor qualification — the #1 job, and background it

The whole point of a submission is the question **"did it actually QUALIFY?"** Run the watcher as a
**background process** and keep working; it polls until the verdict is terminal:

```bash
uv run python "$S" monitor --name crewborg --watch     # run in the BACKGROUND; --poll/--max-minutes to tune
```

It reports the path **submission → membership → verdict**:

- submission `status` → `placed` (+ the `membership` id), or `rejected` with `notes`.
- membership `status`: **`submitted` → `qualifying`** (running qualifier episodes — it prints the
  `completed/scheduled` episode progress) **→ `competing` (✅ QUALIFIED)** or **`disqualified` (❌)**.
- once `competing`: the division **leaderboard rank/score**, and **`is_champion`** if promoted.

**What "qualified" means:** the league's **commissioner** runs qualifier rounds (~every 10 min) and,
per its configured criteria (enough **completed episodes** + a **score** bar), transitions the
membership `qualifying → competing`. There is **no games-played field** on the membership — progress
comes from the membership-events evidence, which the monitor surfaces.

## Reading a DISQUALIFICATION

- **`substatus=crash`** → the policy crashed / failed episodes. **The #1 cause is TIMEOUTS** —
  especially **LLM latency** (a fast or no-LLM player qualifies clean). **Pull the qualifier
  episodes** (`coworld-episode-artifacts`) and read the logs for the timeout/error.
- **`substatus=inactive`** → **evicted** (player-per-user limit, default 2) or **retired** — **not** a
  quality failure (a newer champion of yours can evict an older membership).
- **A failed *round* is NOT a disqualified *policy*.** Infra faults (5xx / OOM / dead pod) abort a
  round without changing membership status. Trust **`membership.status == disqualified`**, never round
  failures — the monitor keys off status for exactly this reason.

## Reversibility & attribution

- **`coworld retire-membership <lpm_id> [--reason …]`** retires a placed membership (`POST
  /v2/league-policy-memberships/{id}/retire`); the public submission record persists. Treat `submit`
  as **irreversible** when deciding to do it — retiring is cleanup, not an undo.
- The CLI `submit` always submits as your **account-default player**. Submitting under a *different*
  owned player identity uses the API `player_id` field (not exposed by the CLI).

## Notes & gotchas

- **Know what you're submitting:** versioning (the version → change log) lives in the **`build &
  upload`** skill + [`version_log.md`](../../crewborg/version_log.md) — confirm `vN` maps to the
  change you intend before this gated step.
- **amd64** images only; **rotating** league/division ids (re-resolve); `submit` only resolves
  policies you own (`--mine`).
- **`resolve-and-upload` is NOT this flow** — it's a Coworld/*game* upload wrapper, not a policy one.
- Routes used: `/v2/league-submissions`, `/v2/league-policy-memberships`,
  `/v2/policy-membership-events`, `/v2/divisions/{id}/leaderboard`. The discipline behind "submit
  rarely" is in [`../../docs/best_practices.md`](../../docs/best_practices.md).
- Full CLI flags + routes + the submission/qualification model: [`references/cli.md`](references/cli.md).
