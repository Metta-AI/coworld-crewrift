---
name: coworld-experience-requests
description: "Use to create and monitor Coworld experience requests — hosted batches of episodes you define (target, roster, roles, count) for evaluating a policy against a live field. Triggers: 'run crewborg vs the top opponents', 'make an experience request', 'request N hosted games', 'A/B a policy against the league', 'measure the imposter', 'set up an evaluation battery'. Pair with coworld-episode-artifacts to pull the resulting episodes."
---

# Coworld Experience Requests

The primary eval instrument: a **hosted batch of episodes you define and the server runs**. You pick
a **target** (game / league / division), a **roster** (which policies play, in which seats and
roles), and a **count**; POST it; poll the `xreq_…` to completion; then pull the episodes with the
`coworld-episode-artifacts` skill and analyze. They run in parallel on Softmax infra and are
currently free — use them liberally, but **target them to the question.**

**Announce at start:** "Setting up a Coworld experience request. I'll frame the question, resolve the
live IDs, compose the request, validate it against the live schema, POST it, and monitor to completion."

> **Check `user_preferences.md` for any standing XP-request preferences** before composing
> (e.g. a preferred opponent set, a default episode count, an always-2-imposter rule). The human may
> have recorded eval defaults there — honor them unless this request's question overrides them.

---

## Step 1 — frame the question, then pick the shape

**The question dictates the roster.** Don't reach for a default body; decide what you're measuring,
then choose the request *kind* below. This is the most important step — a mis-shaped request answers
the wrong question (or gets masked).

| What you want to learn | Request kind | Key knobs |
|---|---|---|
| "How does crewborg do **against the live field**?" | **Field eval** | `top_n`/`random` opponents, all seats rotating (`slot:-1`), **natural roles** (no role override), high `num_episodes` |
| "**Did my change help?**" (vs a baseline) | **A/B** | pin the **full roster** with explicit `policy_ref`s, pin seats, *identical across arms except the subject*. Prefer the **`crewrift-ab`** skill, which runs both arms matched. |
| "How's crewborg **as imposter** (or crew)?" | **Role-pinned eval** | pin your seat + force its role via `game_config_overrides.slots`; opponents rotate the rest |
| "Where's the **role gap** as it actually plays?" | **Natural-roles eval** | no role override (roles fall naturally), seats rotating — see the masking caveat below |
| "Does the build **run at scale / not crash**?" | **Self-play crash-test** | your `policy_ref` in most/all seats, modest `num_episodes` |
| "Broaden / hand-pick the **opponent field**" | any of the above | add `included_players` / `excluded_players` to shape the `top_n`/`random` pool |

Full field reference (every option, with worked example bodies) is in
[`references/api.md`](references/api.md) — **read it before composing a body**, and re-print the live
schema when a route 4xxs (the API drifts).

## Decision points & best practices (these are where requests go wrong)

- **Rotate every non-pinned seat (`slot:-1`).** It cancels per-seat bias so a win rate means
  something. Pin a seat *only* to hold a specific role/position for the question.
- **A single request already varies the field.** `top_n`/`random` seats **re-draw per episode** and
  round-robin seats rotate per episode (verified — see `references/api.md`), so one N-episode request
  faces a varied field across episodes. You do **not** fire multiple requests for opponent variety.
- **Pin roles to answer a role question — but beware masking.** Forcing roles (e.g. crewborg always
  imposter) isolates that role, but a **role-pinned A/B can hide a gap that only shows in natural
  roles**. Confirm a promising role-specific change in a **natural-roles** run before trusting it.
- **For a clean A/B, pin the whole roster** with explicit `policy_ref`s (exact `name:vN`) so both
  arms are identical except the subject — `top_n`/`random` are uncontrolled (the pool drifts and can
  even seat your own entry). Use `included_players`/`excluded_players` to shape the pool when you want
  a specific field without full pinning.
- **Enough episodes, and ops-filter.** Pick `num_episodes` high enough to smooth variance, and drop
  **connect/disconnect-timeout** episodes (score ≤ 0 / `-100`) before computing rates — they gut your
  effective n. If the ops-failure rate is high, re-run.
- **Decompose by role and opponent** when you analyze (an aggregate hides a broken role).

## Step 2 — resolve live IDs (never reuse cached ones; they rotate)

```bash
# run with the Coworld SDK available — a uv env with coworld[auth] + `softmax login`
S=players/crewborg/skills/coworld-experience-requests/scripts/experience_request.py
uv run python "$S" resolve --policy crewborg --version <N>        # a name -> version id(s)
uv run python "$S" resolve --division div_... --top 7             # a division's ranked active field
```
`policy_ref` accepts the `name:vN` label and the target accepts a division/league **name**, so you
often don't need UUIDs at all — `resolve` is mainly for ranking the field and confirming versions.

## Step 3 — compose, validate, create

Compose the body per [`references/api.md`](references/api.md) for the chosen kind (e.g. `/tmp/req.json`),
then validate against the **live** schema before posting (`additionalProperties:false` — a stray key
4xxs):

```bash
uv run python "$S" create /tmp/req.json --check-schema   # dry-run: validate, don't POST
uv run python "$S" create /tmp/req.json                  # POST for real -> prints xreq_… + summary
```
**Verify it resolved as intended:** `episode_count` matches `num_episodes`, and the first episodes'
`participants` seat the policies/versions and roles you intended (the spread you expected).

## Step 4 — monitor, then pull & analyze

"Created" ≠ "done":

```bash
uv run python "$S" monitor xreq_...
```
For several requests at once (a sweep / multi-role eval), `scripts/xp_dashboard.py xreq_... [...]`
serves a self-contained browser dashboard (completion/ETA, win-rate leaderboard overall/crew/imposter,
heatmap, score strips; ops-filtered — watch the "ops-filtered" count). When every child episode is
terminal, pull replays/results/logs with the **`coworld-episode-artifacts`** skill
(`fetch_artifacts.py --xreq xreq_...`) and compute the stats the question needs, **decomposed by role
and opponent**.

## Notes

- Auth comes from `softmax login` (the tool uses `load_current_token`); run inside `uv run`.
- For a one-off you'd rather hand-drive, the `coworld xp-request create|list|get|episodes` CLI hits
  the same routes; this script adds live-schema validation, POST/readback race handling, ID
  resolution, and polling.
- This skill *creates*; **`coworld-episode-artifacts`** *downloads* the episodes it produces;
  **`crewrift-report`** turns a finished batch into a strengths/weaknesses report; **`crewrift-ab`**
  wraps the A/B shape end-to-end.
