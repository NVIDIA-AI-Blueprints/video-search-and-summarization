#!/usr/bin/env bash
# =============================================================================
#  build-fusion.sh — run your working copy of the BEV fusion service.
#
#  The fusion container comes from the registry, so edits to
#  services/rtvi/rt-cv-3d/rt-cv-bev-fusion/src have no effect on a deployment no
#  matter how often you relaunch it. This builds those edits into an image and
#  points the blueprint at it.
#
#    ./build-fusion.sh on      [target]  build :local from the working copy, pin to it
#    ./build-fusion.sh off     [target]  unpin — next launch is on the registry image
#    ./build-fusion.sh apply   [target]  recreate just the fusion container
#    ./build-fusion.sh status  [target]
#
#  target is blueprint (the default) or standalone. The image is the same for
#  both; only the pin and the recreate differ, because the two deployments read
#  the fusion image ref from different places.
#
#  Blueprint pins through overrides.env, which is last in compose's --env-file
#  order and so beats the release default in containers.env. Standalone has no
#  such file, and its BEV_FUSION_IMAGE/TAG live in the .env this script reads
#  the released ref from, so pinning there would erase what `off` restores. Its
#  compose.yml is edited instead: the bev-fusion image line is replaced outright
#  and the original parked in a comment above it, so `off` restores it exactly.
#  Both pins are tracked files, so `git diff` shows them, and both survive
#  --force-recreate. That is the whole difference from `docker cp`-ing a file
#  into the running container, which the next launch silently discards.
#
#  PULL=1 cannot refresh a local image and does not try to; see
#  launch-deployment.sh's pull_images.
# =============================================================================
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

FUSION_SRC="$VSS_REPO/services/rtvi/rt-cv-3d/rt-cv-bev-fusion"
LOCAL_IMAGE="${LOCAL_IMAGE:-vss-rt-cv-mv3dt-bev-fusion}"
LOCAL_TAG="${LOCAL_TAG:-local}"
OVERRIDES="$PROFILE/overrides.env"
KEY_IMAGE=VSS_RT_CV_MV3DT_BEV_FUSION_IMAGE
KEY_TAG=VSS_RT_CV_MV3DT_BEV_FUSION_TAG
SA_COMPOSE="$RTCV/docker/compose.yml"
# The pinned image line replaces a ${BEV_FUSION_IMAGE:-…} interpolation, which
# cannot simply be re-derived: docker/.env sets that variable, so editing the
# fallback would change nothing. The line it replaced is parked in this comment
# instead, which makes `off` an exact restore and keeps the state in the one
# file the pin affects.
SA_MARK="# build-fusion.sh pin, \`off standalone\` restores:"

TARGET="${2:-blueprint}"
case "$TARGET" in
  blueprint|standalone) ;;
  *) die "unknown target: $TARGET (expected blueprint or standalone)" ;;
esac

usage() { sed -n '2,/^# ====/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//; s/^#$//'; }

env_val() { sed -nE "s/^$2=\"?([^\"]*)\"?$/\1/p" "$1" 2>/dev/null | tail -1; }

# released_ref — the image an unpinned deployment runs, and the base to layer
# onto. Taken from the standalone component's .env, which names the same
# release as containers.env but states it literally, where containers.env
# composes it from a registry chosen at deploy time. Never written by this
# script, so `off` always has something true to go back to.
released_ref() {
  local img tag
  img=$(env_val "$RTCV/docker/.env" BEV_FUSION_IMAGE)
  tag=$(env_val "$RTCV/docker/.env" BEV_FUSION_TAG)
  [ -n "$img" ] && [ -n "$tag" ] \
    || die "no BEV_FUSION_IMAGE/TAG in $RTCV/docker/.env — cannot resolve the released image"
  echo "$img:$tag"
}

# Empty, not a failure, when the deployment is unpinned — the caller's `set -e`
# would otherwise take an ordinary "no override" for an error.
pinned_ref() {
  if [ "$TARGET" = standalone ]; then
    grep -qF "$SA_MARK" "$SA_COMPOSE" 2>/dev/null \
      && awk '/^  bev-fusion:/{s=1} s && $1=="image:"{print $2; exit}' "$SA_COMPOSE"
    return 0
  fi
  local img; img=$(env_val "$OVERRIDES" "$KEY_IMAGE")
  [ -n "$img" ] && echo "$img:$(env_val "$OVERRIDES" "$KEY_TAG")"
  return 0
}

# Both edits are scoped to the bev-fusion block by tracking the service header,
# rather than matching `image:` anywhere — the file has one per service.
pin() {
  if [ "$TARGET" = standalone ]; then
    local tmp; tmp=$(mktemp)
    awk -v mark="$SA_MARK" -v ref="$LOCAL_IMAGE:$LOCAL_TAG" '
      /^  [a-zA-Z0-9_-]+:/ { insvc = ($0 ~ /^  bev-fusion:/) }
      insvc && index($0, mark) { have = 1; print; next }
      insvc && $1 == "image:" && !done {
        match($0, /^ */); pad = substr($0, 1, RLENGTH)
        if (!have) print pad mark " " substr($0, RLENGTH + 1)
        print pad "image: " ref
        done = 1; next
      }
      { print }
    ' "$SA_COMPOSE" >"$tmp" && mv "$tmp" "$SA_COMPOSE"
    echo "    ${SA_COMPOSE#$VSS_REPO/} -> $LOCAL_IMAGE:$LOCAL_TAG"
  else
    set_env_key "$OVERRIDES" "$KEY_IMAGE" "$LOCAL_IMAGE"
    set_env_key "$OVERRIDES" "$KEY_TAG"   "$LOCAL_TAG"
  fi
}

unpin() {
  if [ "$TARGET" = standalone ]; then
    if ! grep -qF "$SA_MARK" "$SA_COMPOSE" 2>/dev/null; then
      echo "    ${SA_COMPOSE#$VSS_REPO/} carries no pin"
      return 0
    fi
    local tmp; tmp=$(mktemp)
    awk -v mark="$SA_MARK" '
      /^  [a-zA-Z0-9_-]+:/ { insvc = ($0 ~ /^  bev-fusion:/) }
      insvc && (i = index($0, mark)) {
        match($0, /^ */); pad = substr($0, 1, RLENGTH)
        orig = substr($0, i + length(mark) + 1)
        next
      }
      insvc && $1 == "image:" && orig != "" { print pad orig; orig = ""; next }
      { print }
    ' "$SA_COMPOSE" >"$tmp" && mv "$tmp" "$SA_COMPOSE"
    echo "    ${SA_COMPOSE#$VSS_REPO/} restored"
  else
    sed -i "/^\(${KEY_IMAGE}\|${KEY_TAG}\)=/d" "$OVERRIDES"
    echo "    overrides.env: $KEY_IMAGE / $KEY_TAG removed"
  fi
}

build() {
  local base; base=$(released_ref)
  step "Building $LOCAL_IMAGE:$LOCAL_TAG on top of $base"

  # A layer over the release rather than Dockerfiles/measurement-fusion.Docker-
  # file: that one builds the distroless runtime from scratch — nvcr base, apt,
  # pipenv, a gcc source tarball for license compliance — which is minutes and a
  # working proxy to reproduce bytes we already have. Here the runtime is the
  # released one untouched, with two source files replaced on top, so an edit
  # rebuilds in about a second. CI still builds the real image.
  docker build -t "$LOCAL_IMAGE:$LOCAL_TAG" -f - "$FUSION_SRC" <<EOF
FROM $base
COPY src/schema_pb2.py src/measurement_fusion.py /app/
EOF
}

# apply — recreate the one container, so a fusion change costs seconds instead
# of a full stack relaunch. Needs generated.env, which the last deploy left
# behind; blueprint-deploy.sh's own `down` deletes it.
#
# generated.env is a snapshot of overrides.env taken when the stack came up, so
# on its own it still names whatever was pinned then — a pin made since is
# invisible and the container comes back on the old image, which looks exactly
# like the pin not working. Carry the two keys across before recreating. The
# next full launch regenerates the file from overrides.env anyway.
apply() {
  local pin
  # Standalone needs none of the generated.env dance: the pin is in its
  # compose.yml, which every `docker compose` in that directory reads, so it is
  # already in effect and the container only has to be replaced. Recreating it
  # from the blueprint's compose files instead would be wrong rather than
  # merely useless, since both deployments name the container
  # vss-rtvi-cv-bev-fusion and the blueprint wires it to different topics.
  if [ "$TARGET" = standalone ]; then
    pin=$(pinned_ref)
    step "Recreating vss-rtvi-cv-bev-fusion on ${pin:-$(released_ref)}"
    [ -n "$pin" ] || ylw "  not pinned — this recreates on the released image"
    ( cd "$RTCV/docker" && docker compose up -d --force-recreate bev-fusion )
    return
  fi

  local gen="$PROFILE/generated.env"
  [ -f "$gen" ] || die "no $gen — the stack is down; launch it with launch-deployment.sh instead"
  pin=$(pinned_ref)
  if [ -n "$pin" ]; then
    set_env_key "$gen" "$KEY_IMAGE" "${pin%:*}" >/dev/null
    set_env_key "$gen" "$KEY_TAG"   "${pin##*:}" >/dev/null
  else
    sed -i "/^\(${KEY_IMAGE}\|${KEY_TAG}\)=/d" "$gen"
  fi
  step "Recreating vss-rtvi-cv-bev-fusion on ${pin:-$(released_ref)}"
  ( cd "$VSS_REPO/deploy/docker" && docker compose \
      -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
      --env-file containers.env --env-file "$PROFILE/.env" --env-file "$gen" \
      up -d --force-recreate vss-rtvi-cv-bev-fusion )
}

status() {
  local pin run; pin=$(pinned_ref)
  # Both deployments name the container the same, so this line is true whichever
  # one is up — but the pin above it is only read for $TARGET. A standalone stack
  # running :local looks unpinned under `status blueprint`.
  run=$(docker inspect vss-rtvi-cv-bev-fusion --format '{{.Config.Image}}' 2>/dev/null || true)
  echo "  target:    $TARGET"
  echo "  released:  $(released_ref)"
  echo "  pinned:    ${pin:-<none — $TARGET runs the released image>}"
  echo "  running:   ${run:-<not running>}"
  [ -n "$pin" ] && [ -n "$run" ] && [ "$run" != "$pin" ] \
    && ylw "  the running container predates the pin — ./build-fusion.sh apply $TARGET"
  return 0
}

case "${1:-}" in
  ""|-h|--help|help) usage ;;
  on)
    build
    step "Pinning $TARGET to $LOCAL_IMAGE:$LOCAL_TAG"
    pin
    grn "
Built and pinned. Apply it to the running stack with:
  ./build-fusion.sh apply $TARGET

Re-run \`on\` after every source edit: the pin is by tag, so an edit on its own
changes nothing. Check what the container came up with:
  docker logs vss-rtvi-cv-bev-fusion 2>&1 | grep 'Fusion method'" ;;
  off)
    step "Removing the $TARGET pin"
    unpin
    grn "
Unpinned. The next launch runs $(released_ref) again." ;;
  apply)  apply ;;
  status) status ;;
  *)      die "unknown action: $1 (expected on, off, apply or status)" ;;
esac
