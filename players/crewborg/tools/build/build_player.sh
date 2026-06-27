#!/usr/bin/env bash
# Build the crewborg player image (linux/amd64) — the Coworld upload contract.
#
# Usage: tools/build/build_player.sh [--tag REF] [--push REF] [--sdk-ref REF]
#   --tag      image tag to build               (default: crewborg:dev)
#   --push     re-tag the built image as REF and `docker push` it
#   --sdk-ref  override PLAYERS_SDK_REF for this one build
#
# WHAT IT DOES: builds players/crewborg/crewborg/coworld/Dockerfile with the player dir as
# the build context (so the Dockerfile's `COPY crewborg …` resolves). All inputs are
# public, so the host needs only Docker — no credentials: the Dockerfile pip-installs the
# shared SDK from the public coworld-tools repo (players/ subdir) at PLAYERS_SDK_REF and
# copies the crewborg package; the image CMD launches `python -m crewborg.coworld.policy_player`.
#
# HOW TO EDIT: the version pins live in tools/build/versions.env (one source of truth) —
# change PLAYERS_SDK_REF there, not here. This script is crewborg-specific by design;
# to build another player, copy it and point at that player's Dockerfile. After building,
# upload with the `build-and-upload` skill.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # players/crewborg/tools/build
PLAYER_DIR="$(cd "$HERE/../.." && pwd)"                 # players/crewborg  (the build context)
# shellcheck source=versions.env
source "$HERE/versions.env"

die() { echo "build_player.sh: $*" >&2; exit 1; }

tag="crewborg:dev"; push_ref=""
while (( $# )); do
  case "$1" in
    --tag)     tag="$2";             shift 2 ;;
    --push)    push_ref="$2";        shift 2 ;;
    --sdk-ref) PLAYERS_SDK_REF="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *)         die "unknown arg '$1' (see --help)" ;;
  esac
done

dockerfile="$PLAYER_DIR/crewborg/coworld/Dockerfile"
[ -f "$dockerfile" ] || die "no Dockerfile at crewborg/coworld/Dockerfile under $PLAYER_DIR"

# Resolve PLAYERS_SDK_REF=main → coworld-tools' current main commit, so the pip-install
# Docker layer's cache busts exactly when main moves (a literal `main` build-arg caches on
# the unchanged URL string and silently keeps a stale SDK layer).
if [ "$PLAYERS_SDK_REF" = "main" ]; then
  remote_sha="$(git ls-remote https://github.com/Metta-AI/coworld-tools.git refs/heads/main | awk '{print $1}' | head -1)"
  if [ -n "$remote_sha" ]; then
    echo "==> PLAYERS_SDK_REF=main resolved to coworld-tools main $remote_sha"
    PLAYERS_SDK_REF="$remote_sha"
  else
    echo "WARNING: could not resolve coworld-tools main via ls-remote; building at 'main' (Docker may reuse a stale cached SDK layer)" >&2
  fi
fi

echo "==> docker buildx build --platform=linux/amd64 -t $tag  (context: $PLAYER_DIR)"
docker buildx build --platform=linux/amd64 --load \
  -f "$dockerfile" \
  -t "$tag" \
  --build-arg "PLAYERS_SDK_REF=$PLAYERS_SDK_REF" \
  "$PLAYER_DIR"

if [ -n "$push_ref" ]; then
  echo "==> docker tag $tag $push_ref && docker push $push_ref"
  docker tag "$tag" "$push_ref"
  docker push "$push_ref"
fi

cat <<EOF

Built: $tag  (linux/amd64)
Next:  upload it — the \`build-and-upload\` skill, or
       uv run coworld upload-policy $tag --name crewborg --run python --run -m --run crewborg.coworld.policy_player
EOF
