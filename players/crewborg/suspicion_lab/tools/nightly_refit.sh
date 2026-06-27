#!/usr/bin/env bash
# Nightly suspicion refit → new crewborg champion (James's standing instruction,
# 2026-06-12). Runs from cron around midnight:
#
#   1. scrape yesterday's league rounds into the corpus + expand replays
#   2. rebuild the dataset and refit the runtime model (full corpus)
#   3. GATES: CV AUC + corpus-size sanity, then the crewborg test suite,
#      then a local Gate-1 smoke — ANY failure aborts with the current
#      champion untouched
#   4. vendor the new weights, rebuild the image, upload as a new crewborg
#      version, submit it to the Crewrift league, commit the weights + a
#      version-log line
#
# Usage:  nightly_refit.sh [--check]     (--check verifies prerequisites only)
# Logs:   suspicion_lab/logs/nightly-<date>.log
set -uo pipefail

# The player root (players/crewborg) — derived from this script's location, not hardcoded,
# so the cron loop works wherever the repo is checked out (suspicion_lab/tools/ -> ../..).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

SUSPICION_LAB="$REPO/suspicion_lab"
WEIGHTS_DST="$REPO/crewborg/data/suspicion_weights.json"
VERSION_LOG="$REPO/crewborg/version_log.md"
LOG_DIR="$SUSPICION_LAB/logs"
TAG="nightly-$(date +%F)"
LOG="$LOG_DIR/$TAG.log"
AUC_MIN="0.70"        # sanity floor — a fit below this never ships
GAMES_MIN="500"       # corpus floor — don't ship a fit on a thin corpus

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1
cd "$REPO"

log() { echo "[$(date '+%F %T')] $*"; }
abort() { log "ABORT: $* — champion unchanged."; exit 1; }

step() {  # step <name> <cmd...>
  local name="$1"; shift
  log "step: $name"
  "$@" || abort "$name failed (exit $?)"
}

log "=== nightly refit start ($TAG) ==="

# --- prerequisites -------------------------------------------------------------
command -v uv >/dev/null || abort "uv not on PATH"
command -v docker >/dev/null || abort "docker not on PATH"
docker info >/dev/null 2>&1 || abort "docker daemon not running"
# NB: capture-then-match, not `| grep -q` — pipefail + grep -q's early exit makes
# the pipeline fail on SIGPIPE even when the match succeeds.
AUTH_STATUS="$(uv run softmax status 2>/dev/null || true)"
[[ "$AUTH_STATUS" == *"Authenticated"* ]] || abort "softmax auth expired — run: uv run softmax login"
if [[ "${1:-}" == "--check" ]]; then log "prerequisites OK (--check)"; exit 0; fi

# --- 1. scrape + expand ----------------------------------------------------------
step "scrape" uv run python suspicion_lab/tools/scrape_corpus.py --max-rounds 150
step "expand" uv run python suspicion_lab/tools/expand_corpus.py --workers 8

# --- 2. dataset + fit -------------------------------------------------------------
step "dataset" uv run python suspicion_lab/tools/build_dataset.py
step "fit" uv run python suspicion_lab/tools/fit.py --tag "$TAG" --features runtime

WEIGHTS_SRC="$SUSPICION_LAB/models/$TAG/suspicion_weights.json"
[[ -f "$WEIGHTS_SRC" ]] || abort "fit produced no weights file"

# --- 3. gates -----------------------------------------------------------------------
read -r AUC GAMES <<<"$(python3 -c "
import json
w = json.load(open('$WEIGHTS_SRC'))
print(w.get('cv_auc', 0), w.get('games', 0))")"
log "fit: AUC=$AUC games=$GAMES"
python3 -c "exit(0 if float('$AUC') >= $AUC_MIN else 1)" || abort "AUC $AUC below floor $AUC_MIN"
python3 -c "exit(0 if int('$GAMES') >= $GAMES_MIN else 1)" || abort "corpus $GAMES games below floor $GAMES_MIN"

cp "$WEIGHTS_DST" "$WEIGHTS_DST.bak"
cp "$WEIGHTS_SRC" "$WEIGHTS_DST"
restore() { cp "$WEIGHTS_DST.bak" "$WEIGHTS_DST"; log "weights restored"; }

if ! uv run pytest crewborg/tests -q; then restore; abort "test suite failed on new weights"; fi

step "build" tools/build/build_player.sh
if ! uv run python skills/coworld-local-run/scripts/smoke.py \
      --coworld crewrift --image crewborg:dev \
      --run python --run=-m --run crewborg.coworld.policy_player --timeout 240; then
  restore; abort "Gate-1 smoke failed"
fi

# --- 4. upload + submit + record ------------------------------------------------------
UPLOAD_OUT="$(uv run coworld upload-policy crewborg:dev --name crewborg \
  --run python --run=-m --run crewborg.coworld.policy_player \
  --secret-env CREWBORG_METRICS=1 \
  --secret-env CREWBORG_TRACE_GROUPS=voting,action,decision \
  --secret-env CREWBORG_TRACE_DECISION_FIELDS=phase,role,mode,intent,command,voting,self \
  --secret-env CREWBORG_CHAT_NLP=1 2>&1)" || { restore; abort "upload failed: $UPLOAD_OUT"; }
VERSION="$(echo "$UPLOAD_OUT" | grep -oE 'crewborg:v[0-9]+' | tail -1)"
[[ -n "$VERSION" ]] || { restore; abort "could not parse uploaded version from: $UPLOAD_OUT"; }
log "uploaded $VERSION"

LEAGUE_ID="$(uv run coworld leagues --json 2>/dev/null | python3 -c "
import json,sys
print(next(l['id'] for l in json.load(sys.stdin) if l['name'] == 'Crewrift'))")" || { restore; abort "league resolve failed"; }
step "submit" uv run coworld submit "$VERSION" --league "$LEAGUE_ID"
log "submitted $VERSION to $LEAGUE_ID"

# Version-log line + commit (best effort — a dirty tree must not block the ship).
python3 - "$VERSION" "$TAG" "$AUC" "$GAMES" <<'EOF' || log "WARN: version_log append failed"
import sys
version, tag, auc, games = sys.argv[1:5]
path = "crewborg/version_log.md"
lines = open(path).read().split("\n")
row = (f"| {version.split(':')[1]} | (see policy page) | {tag[8:]}Z | nightly_refit.sh (automated) | std env | "
       f"**Nightly suspicion refit** ({tag}): same code, re-fitted weights on the full corpus "
       f"({games} games, CV AUC {auc}). Gates passed: AUC/corpus floors, test suite, Gate-1 smoke. "
       f"Auto-submitted to the Crewrift league per James's standing instruction (2026-06-12). |")
for i, line in enumerate(lines):
    if line.startswith("| v"):
        lines.insert(i, row)
        break
open(path, "w").write("\n".join(lines))
EOF
rm -f "$WEIGHTS_DST.bak"
git add "$WEIGHTS_DST" "$VERSION_LOG" 2>/dev/null \
  && git commit -q -m "nightly refit: $VERSION (AUC $AUC, $GAMES games) — auto-fitted, gated, submitted

Co-Authored-By: nightly_refit.sh (Claude-built automation)" \
  || log "WARN: git commit skipped (dirty tree or lock)"

log "=== nightly refit complete: $VERSION shipped ==="
