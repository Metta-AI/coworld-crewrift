#!/usr/bin/env bash
# SessionStart hook: rotate crewborg's tentative-lessons buffer.
#
# Mechanism, not trust: on every NEW session (source = startup|clear — never
# resume/compact), the previous session's buffer is archived with a timestamp and
# a fresh, stamped buffer is created. The agent is pointed at it via
# additionalContext. Also records the fresh buffer's hash keyed by session_id so
# the Stop-hook nudge (lessons_stop_nudge.sh) can tell "untouched this session".
#
# Self-locating: paths are derived relative to this script (tools/ sits one level
# under the player root), so it works regardless of the agent's cwd.
#
# Stdin: hook JSON {session_id, source, ...}. Stdout: hook JSON (additionalContext).
set -uo pipefail

PLAYER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUFFER="$PLAYER_ROOT/docs/TENTATIVE_LESSONS.md"
ARCHIVE_DIR="$PLAYER_ROOT/docs/lessons_archive"
DISPLAY="players/crewborg/docs/TENTATIVE_LESSONS.md"
STATE_DIR="${TMPDIR:-/tmp}"

INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)"
SOURCE="$(printf '%s' "$INPUT" | jq -r '.source // "startup"' 2>/dev/null || echo startup)"

emit_context() {
  jq -n --arg ctx "$1" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
}

record_state() {  # let the Stop nudge detect "buffer untouched this session"
  { md5 -q "$BUFFER" 2>/dev/null || md5sum "$BUFFER" 2>/dev/null | cut -d' ' -f1; } \
    > "$STATE_DIR/crewborg_lessons_baseline_$SESSION_ID" 2>/dev/null || true
}

write_fresh_buffer() {
  cat > "$BUFFER" << 'EOF'
# crewborg tentative lessons — session buffer

This is THIS SESSION's lesson buffer. Write candidate lessons here **as you go** — eagerly and
noisily; most will be noise and that's fine. At the next session start, the rotation hook archives
this file automatically to [`lessons_archive/`](lessons_archive/) and creates a fresh one — nothing
you write here is lost, and nothing carries over by hand.

**Lifecycle.** Per-session buffer → automatic archive (SessionStart hook,
`../tools/rotate_lessons.sh`) → periodic human+agent review (the `/lessons-review` skill) that
clusters RECURRING lessons across archived sessions and graduates the keepers into
[`best_practices.md`](best_practices.md). **Recurrence across independent session buffers — not
in-session hit counts — is the graduation signal.** A Stop hook (`../tools/lessons_stop_nudge.sh`)
nudges once per session if substantive work ends with this buffer untouched.

**Entry format.** `### <lesson, one line>` then `Evidence:` (what you observed, concrete) and an
optional `Status:` note. Terse. One lesson per `###`.

---
EOF
}

if [[ "$SOURCE" != "startup" && "$SOURCE" != "clear" ]]; then
  # Resumed/compacted session: same session, do NOT rotate. Just point at the buffer.
  [[ -f "$STATE_DIR/crewborg_lessons_baseline_$SESSION_ID" ]] || record_state
  emit_context "Tentative-lessons buffer (this session's, write lessons AS YOU GO): $DISPLAY"
  exit 0
fi

STAMP="$(date '+%Y%m%d-%H%M%S')"
mkdir -p "$ARCHIVE_DIR"
ARCHIVED=""
if [[ -f "$BUFFER" ]] && grep -q '^### ' "$BUFFER"; then
  ARCHIVE_FILE="$ARCHIVE_DIR/TENTATIVE_LESSONS-$STAMP.md"
  mv "$BUFFER" "$ARCHIVE_FILE"
  ARCHIVED="$(basename "$ARCHIVE_FILE")"
fi

write_fresh_buffer

if [[ -n "$ARCHIVED" ]]; then
  CTX="Tentative-lessons buffer rotated: previous session's lessons archived to players/crewborg/docs/lessons_archive/$ARCHIVED. Fresh buffer: $DISPLAY — write candidate lessons there AS YOU GO (a Stop hook will nudge once if substantive work happens with the buffer untouched)."
else
  CTX="Fresh tentative-lessons buffer: $DISPLAY (previous buffer was empty; nothing archived) — write candidate lessons there AS YOU GO."
fi

record_state

# Keep git tidy: commit the rotation if the tree allows it (best effort, never block).
if [[ -n "$ARCHIVED" ]]; then
  git -C "$PLAYER_ROOT" add "$BUFFER" "$ARCHIVE_DIR" >/dev/null 2>&1 \
    && git -C "$PLAYER_ROOT" commit -q -m "lessons: rotate crewborg session buffer -> lessons_archive/$ARCHIVED (SessionStart hook)" \
       -- "$BUFFER" "$ARCHIVE_DIR" >/dev/null 2>&1 \
    || true
fi

emit_context "$CTX"
