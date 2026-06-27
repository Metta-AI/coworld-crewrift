# Version Log

A running record of every **uploaded policy version** → the code and runtime
configuration it carried, and how it performed. Keep this current as you optimize:
each upload is an experiment, and an attributable version log is what lets the next
agent (or you, later) reconstruct *what actually shipped and why* without re-deriving
it from chat history or the Observatory.

**Why bother.** Experience-request evals and league standings only mean something when
you can map a result back to an exact build + flag set. The platform dedups images by
digest and the image bakes no behavior env, so the *same* image uploaded under
different `--secret-env` flags becomes distinct versions — this log is the only place
that mapping lives.

## How to use it

Add a row per upload, newest at the top. Suggested columns:

- **Version** — your label (`v1`, `v2`, …); mark champions / submitted ships.
- **Policy version ID** — returned by the upload (or "see policy page").
- **Uploaded (UTC)** — when.
- **Source** — the commit / branch / build command the image came from.
- **Runtime config** — the `--run` + `--secret-env` flags baked at upload
  (`CREWBORG_*`, `--use-bedrock`, trace groups, …).
- **Notes / result** — what changed and why, the eval / A-B verdict, and whether it
  was submitted to a league.

> This file ships **empty by design** — keeping a version log is a habit to adopt, not a
> record to inherit. Prior crewborg version history is intentionally not carried into
> this package. Start your own log below.

| Version | Policy version ID | Uploaded (UTC) | Source | Runtime config | Notes / result |
|---|---|---|---|---|---|
| _(your first upload)_ | | | | | |
