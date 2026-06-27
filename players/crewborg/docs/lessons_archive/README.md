# Tentative-lessons archive

One file per past session's lesson buffer, rotated here **automatically** by the SessionStart hook
(`../../tools/rotate_lessons.sh`) — the live buffer is always [`../TENTATIVE_LESSONS.md`](../TENTATIVE_LESSONS.md).
Reviewed buffers move to `reviewed/`.

**Why a buffer + archive instead of one running file.** A script can't write good lessons, but it
*can* guarantee they're captured: each session gets a fresh buffer (so writing is cheap and noisy),
and nothing is ever lost (old buffers are archived, not overwritten). Durable knowledge only enters
[`../best_practices.md`](../best_practices.md) after it has **recurred across independent sessions** —
that recurrence, not a single good idea, is the graduation signal.

**Review cadence:** ≈weekly via the **`/lessons-review`** skill — it clusters lessons that recur
across these archived buffers, proposes promote/keep/cull with recurrence counts, and graduates
keepers into `best_practices.md` on the human's call (then retires the reviewed buffers to `reviewed/`).

## Wiring the hooks

The lifecycle is two Claude Code hooks (mechanism, not trust). Wire them in your settings (e.g. the
`coworld-crewrift` repo's `.claude/settings.json`), pointing at the scripts in `../../tools/`:

```jsonc
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command",
      "command": "players/crewborg/tools/rotate_lessons.sh" }] }],
    "Stop":         [{ "hooks": [{ "type": "command",
      "command": "players/crewborg/tools/lessons_stop_nudge.sh" }] }]
  }
}
```

- **SessionStart** (`rotate_lessons.sh`): on each new session, archives the previous buffer with a
  timestamp, creates a fresh one, and points the agent at it. Resumed/compacted sessions don't rotate.
- **Stop** (`lessons_stop_nudge.sh`): blocks once per session if substantive work happened but the
  buffer is still untouched, so lessons actually get written.

Both scripts locate their paths relative to themselves, so they work regardless of the agent's cwd.
