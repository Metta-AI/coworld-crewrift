#!/usr/bin/env bash
# ==============================================================================
# build_expand_replay.sh — build a VERSION-MATCHED `expand_replay` binary for
#                          reading recorded Crewrift replays.
# ==============================================================================
#
# WHAT IT DOES
#   Builds the crewrift `tools/expand_replay.nim` helper from THIS game repo's own
#   Nim source, at a chosen game commit (`--ref`). `expand_replay` RE-STEPS a
#   recorded replay through the crewrift `sim` and HASH-CHECKS every tick, so it
#   only expands a replay fully when built from the SAME game commit that recorded
#   it. On a version mismatch it ABORTS mid-replay (`trace_complete:false`,
#   `message:"hash failed"`), emitting only sparse early events and no kills /
#   bodies / votes. That version coupling is the #1 silent failure when reading
#   replays — see the "VERSION COUPLING" section below and the event-warehouse
#   README (tools/event-warehouse/crewrift-event-warehouse/README.md, section
#   "expand_replay version coupling").
#
#   This player package (players/crewborg) lives INSIDE the coworld-crewrift game
#   repo, so the Nim source is right here at the repo root: `tools/expand_replay.nim`
#   plus `src/crewrift/{sim,replays}.nim`. We do NOT vendor a prebuilt binary and we
#   do NOT clone — we export the repo's tree at `--ref` with `git archive` (read-only;
#   never touches your working tree or its checked-out commit), resolve Nim deps with
#   `nimby`, and compile host-native. It is a HOST analysis tool you run locally to
#   read replays, so it builds for this host's arch (no Docker, no amd64). The
#   crewrift source + its bitworld dep are public, so no credentials are needed.
#
# HOW TO USE
#   Build at the pinned game ref (default CREWRIFT_REF from tools/build/versions.env):
#     tools/build_expand_replay.sh
#
#   Build at a specific game commit, to a chosen path:
#     tools/build_expand_replay.sh --ref 42fed21 --out /tmp/expand-42fed21
#
#   Build, then VERIFY against a real replay (the only trustworthy version check):
#     tools/build_expand_replay.sh --run /path/to/replay.json
#   A correct binary prints `trace_complete:true` and exits 0; a version-skewed one
#   prints a hash-fail warning with `trace_complete:false`.
#
#   Point the event-warehouse build at the binary you produced:
#     export CREWRIFT_EXPAND_REPLAY="$(tools/build_expand_replay.sh --ref 42fed21 >/dev/null && echo .../expand_replay-42fed21)"
#   (or just pass --out a stable path and export that path).
#
# VERSION COUPLING — finding the RIGHT --ref
#   The platform exposes no git commit for the deployed game image, so you resolve it:
#     1. `coworld episodes --round <id> --json` -> each episode's `coworld_version`
#        (e.g. `crewrift:0.1.54` or `crewrift_prime:0.3.9`) — a published image
#        version, NOT a git tag.
#     2. Map that image to its commit (e.g. via `coworld download <cow_id>`: the
#        runtime image's `/bin/crewrift` mtime pins the build time; pick the crewrift
#        commit at/just-before it). Known mappings (RE-VERIFY before trusting):
#          crewrift:0.1.54        => 42fed21  (arena,  2026-06-24)
#          crewrift_prime:0.3.9   => 20e3be4  (Prime)
#     3. VERIFY: build at that ref and `--run` it on a real replay from that round.
#        Exit 0 + `trace_complete:true` == correct. Anything else == wrong commit.
#   Do NOT build at `master`/`main`: it runs ahead of what's deployed and hash-fails
#   on fresh replays. The default ref lives in ONE place — tools/build/versions.env
#   (CREWRIFT_REF). When fresh replays start hash-failing, that ref needs a bump.
#
# HOW TO EDIT
#   * Default ref: change CREWRIFT_REF in tools/build/versions.env, not here.
#   * Compile recipe: see the `nim c` invocation in build_native(). The flags
#     (-d:release -d:useMalloc --opt:speed) mirror the event-warehouse README; keep
#     them in sync with that doc if you change them.
#   * Toolchain: needs `nim` + `nimby` on PATH (we add ~/.local/bin and
#     ~/.nimby/nim/bin). `nimby sync` regenerates nim.cfg and fetches deps into the
#     exported source tree; nim.cfg is gitignored, which is why `git archive` alone
#     is not enough and the sync step is required.
#   * Args: parsed in the while-loop below. Add a flag there + document it in HOW TO USE.
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # players/crewborg/tools
PLAYER_DIR="$(cd "$HERE/.." && pwd)"                    # players/crewborg
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"               # coworld-crewrift (game repo root)
VERSIONS_ENV="$HERE/build/versions.env"

die() { echo "build_expand_replay.sh: $*" >&2; exit 1; }

# Default game ref comes from the single source of truth.
# shellcheck source=build/versions.env
[ -f "$VERSIONS_ENV" ] || die "missing $VERSIONS_ENV (expected CREWRIFT_REF there)"
source "$VERSIONS_ENV"
[ -n "${CREWRIFT_REF:-}" ] || die "CREWRIFT_REF not set in $VERSIONS_ENV"

ref="$CREWRIFT_REF"
out_bin=""
run_replay=""
while (( $# )); do
  case "$1" in
    --ref)   ref="$2";        shift 2 ;;
    --out)   out_bin="$2";    shift 2 ;;
    --run)   run_replay="$2"; shift 2 ;;
    -h|--help) sed -n '2,72p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

# Default output path: player-local, ref-stamped so multiple game versions coexist.
[ -n "$out_bin" ] || out_bin="$HERE/bin/expand_replay-$ref"

# Sanity: this script only makes sense inside the crewrift game repo.
[ -f "$REPO_ROOT/tools/expand_replay.nim" ] \
  || die "no $REPO_ROOT/tools/expand_replay.nim — is this still inside the coworld-crewrift repo?"
git -C "$REPO_ROOT" cat-file -e "${ref}^{commit}" 2>/dev/null \
  || die "ref '$ref' is not a commit in $REPO_ROOT (fetch it first, or fix --ref)."

# Nim toolchain (host-native build). nimby installs nim under ~/.nimby/nim/bin;
# user installs often live in ~/.local/bin. Make both reachable, then verify.
export PATH="$HOME/.local/bin:$HOME/.nimby/nim/bin:$PATH"
command -v nim   >/dev/null 2>&1 || die "nim not found (install via nimby; see the crewrift repo README)"
command -v nimby >/dev/null 2>&1 || die "nimby not found (https://github.com/treeform/nimby)"

# Export the repo's tree AT THE REF into a throwaway dir — read-only on your checkout
# (no worktree mutation, no branch/commit change). nim.cfg is gitignored, so the
# archive lacks it and the deps; `nimby sync` regenerates nim.cfg and fetches deps.
src_dir="$(mktemp -d)"
trap 'rm -rf "$src_dir"' EXIT
echo "==> exporting $REPO_ROOT @ $ref (git archive; your checkout is untouched)"
git -C "$REPO_ROOT" archive --format=tar "$ref" | tar -x -C "$src_dir"
[ -f "$src_dir/tools/expand_replay.nim" ] || die "export missing tools/expand_replay.nim at $ref"

build_native() {
  echo "==> nimby sync (regenerates nim.cfg + fetches deps; cache hit unless nimby.lock changed)"
  ( cd "$src_dir" && nimby --global sync nimby.lock )

  echo "==> compiling expand_replay (host-native) -> $out_bin"
  mkdir -p "$(dirname "$out_bin")"
  ( cd "$src_dir" && nim c -d:release -d:useMalloc --opt:speed \
      --nimcache:"$(mktemp -d)" \
      --out:"$out_bin" \
      tools/expand_replay.nim )
}
build_native

echo ""
echo "Built: $out_bin   (host-native; game ref $ref)"
echo "Use it:  export CREWRIFT_EXPAND_REPLAY='$out_bin'"
if [[ -n "$run_replay" ]]; then
  echo "==> verifying on $run_replay (expect trace_complete:true, exit 0)"
  exec "$out_bin" "$run_replay"
fi
echo "Verify: $out_bin <replay.json>   # must print trace_complete:true (see VERSION COUPLING)"
