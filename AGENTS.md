# Repository Guidance

## Synchronization And Scope

Before repository work, run `git fetch origin` to inspect current remote state.
Do not implicitly merge or rebase feature work or dirty checkouts. Keep existing
repository instructions and ownership boundaries in force.

This diagnostic policy applies only to this repository's first-party adapter,
game and QA tooling. It does not authorize changes to vendor/third-party code,
Metta, Fabric, Fabric Research (including evidence/checkouts), Polyworld or their
vendored copies. It is guidance only, not a change to runtime output defaults.

## Disposable QA Storage

- Default disposable QA logs, screenshots, frame dumps, browser reports and
  one-off audits to the platform OS temporary directory in a uniquely created
  per-run directory named `coworld-crewrift-qa-<unique-run-id>`.
  Resolve OS temp through existing helpers or `tempfile.gettempdir()`; use
  `mkdtemp` / an equivalent unique-directory helper, not a shared fixed path.
- Honor explicit output/evidence paths and URIs. Retention is opt-in; never
  silently redirect, move or delete existing evidence. Saved games, model
  checkpoints and retained research inputs/outputs are excluded.
- Disposable captures are distinct from live sockets, IPC, status pointers,
  leases, databases and runtime data. Keep those in their required locations
  and preserve consumers, permissions and lifetimes. A path under OS temp is
  not by itself evidence that its contents are disposable.
- Bound diagnostic runs by duration/count and supported size limits. Check
  free space before large captures and report the actual artifact directory.
- OS temp often shares the checkout's disk: this does not reduce live disk
  consumption. Neither macOS temporary directories nor `/tmp` are guaranteed
  to clear on reboot; do not promise reboot cleanup.
- This policy does not authorize cleanup. Any later cleanup requires explicit
  authorization and fresh ownership/activity checks. Never prune global temp
  storage or another task's active outputs.

## Application-Specific Boundaries

- Disposable simulation/replay smoke logs, Sprite viewer screenshots and frame
  dumps belong in QA scratch. See `src/crewrift/server.nim`
  (`saveReplayPath`, `saveScoresPath`) and `src/crewrift/replays.nim`.
- Honor explicit replay-save, replay-load and score paths and runtime artifact
  destinations. Replay files used for regression/research, optimizer histories,
  policy models and saved games are not QA scratch. Keep WebSocket transport and
  live simulation/control state distinct from captured logs.

## Protected Coding-Agent Archives

Codex and Claude sessions, histories, prompts, traces, trajectories, recovery
exports, indexes and databases are archival research data, never disposable QA
output, regardless of their location. Protection includes these absolute paths
and patterns on this workspace host:

- `/home/relh/.codex/sessions/` and `/home/relh/.codex/*.sqlite*`
- `/home/relh/.local/share/codex-accounts/*/sessions/` and account history,
  session indexes, trace databases and recovery exports beneath that tree
- `/home/relh/.claude/`, including `projects/`, `history.jsonl` and any
  session, prompt, trace, recovery, index or database archives
- Equivalent archives in custom `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, other
  account homes or other locations

Never delete, truncate, prune, vacuum, rotate, rewrite or relocate these archives
under this policy, and never redirect coding-agent storage into OS temp.
When space is constrained, report archive sizes and leave them untouched.
Moving/copying requires explicit direction and a verified backup; deletion
requires a separate explicit request naming the protected data.
