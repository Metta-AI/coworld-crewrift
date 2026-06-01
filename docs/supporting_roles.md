# Crewrift Supporting Roles

Most policy authors only need the rules, play guide, player guide, and bundled
baseline player. The supporting roles below are declared in the Coworld
manifest so agents can see what Crewrift expects around an episode after the
player containers run.

## Optimizer

The optimizer is a local workbench for improving policy code. It uses the
Crewrift manifest, public game docs, episode artifacts, reporter outputs,
grader scores, and diagnoser advice to suggest or test policy changes. Policy
authors do not need to run the optimizer to play a league episode.

## Optimizer Inputs

Useful optimizer-facing inputs for Crewrift are:

- `coworld_manifest.json`: game config schema, variants, bundled images, role
  metadata, and artifact schemas.
- `docs/rules.md`: task, kill, report, meeting, vote, and scoring rules.
- `docs/play_crewrift.md`: local episode flow and public player setup guide.
- Supporting-role artifacts: report summaries, grader scores, and diagnoser
  notes for the target policy.

## Commissioner

The commissioner schedules Crewrift league episodes and updates league
placement. Crewrift uses the shared social-deduction commissioner behavior: it
rotates seats across submitted policies, tracks match placement, and keeps
league state separate from the game container.

## Reporter

The reporter turns completed episode artifacts into human-readable summaries.
Crewrift currently uses the Softmax default reporter, which reads result scores
and emits a compact Markdown summary for status and debugging surfaces.

## Grader

The grader consumes episode results and emits scalar evaluation signals. For
Crewrift, useful grading signals include win/loss, score spread, task progress,
kills, body reports, votes, skip votes, timeouts, survival, and whether the
policy stayed connected.

## Diagnoser

The diagnoser consumes a target policy plus optional Crewrift episode artifacts,
then writes policy-assay advice for the agent improving that policy. Good
diagnoser output points to a specific failure mode such as connection failure,
bad navigation, missing task execution, weak meeting behavior, or timeouts.
