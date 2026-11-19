#!/usr/bin/env bash
# K9X Inspector — pull-based, idempotent updater. Runs on the host, never in the container.
#
# Started by the systemd path unit (ubuntu/build-run.sh install-updater) when the UI's Update button writes
# runtime/update/request.json, or by hand: ubuntu/build-run.sh update.
#
#   1. Check: local commit vs origin, installed k9-aif (in the running container) vs latest on PyPI.
#      Both current -> "up_to_date", nothing is built or restarted.
#   2. git pull --ff-only (as the repository's owner), tag the running image :previous.
#   3. Build (build-run.sh build: latest k9-aif, commit baked in). A failed build leaves the old container running.
#   4. Restart and wait for /api/health. Unhealthy -> retag :previous as :latest, restart it: "rolled_back".
#
# Outbound calls only (GitHub, PyPI). Result in runtime/update/status.json (read by the About page); log in
# runtime/update/update.log.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="k9x-inspector:latest"; PREVIOUS="k9x-inspector:previous"; CONTAINER="k9x-inspector"; PORT=8115
RUNTIME_HOST_DIR="${HOME}/containers/volumes/k9x-inspector/runtime"     # same as build-run.sh
UPD="$RUNTIME_HOST_DIR/update"
OWNER="$(stat -c %U "$PROJECT_DIR" 2>/dev/null || stat -f %Su "$PROJECT_DIR")"
PODMAN="sudo podman"; [[ $(id -u) -eq 0 ]] && PODMAN="podman"

as_owner() { if [[ $(id -u) -eq 0 && "$OWNER" != root ]]; then runuser -u "$OWNER" -- "$@"; else "$@"; fi; }
git_() { as_owner git -C "$PROJECT_DIR" "$@"; }

mkdir -p "$UPD"
REQUESTED_BY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("user","?"))' "$UPD/request.json" 2>/dev/null || echo host)"
rm -f "$UPD/request.json"          # taken (also stops the path unit re-triggering)

exec 9>"$UPD/.lock"
if ! flock -n 9; then echo "$(date -Is) another update is running; request by $REQUESTED_BY dropped" >>"$UPD/update.log"; exit 0; fi
exec >>"$UPD/update.log" 2>&1

STATE=""
status() {   # state message [from] [to]
  STATE="$1"
  python3 - "$UPD/status.json" "$1" "$2" "${3:-}" "${4:-}" "$REQUESTED_BY" <<'PY'
import json, os, sys, time
path, state, message, frm, to, user = sys.argv[1:7]
tmp = path + ".tmp"
json.dump({"state": state, "message": message, "from": frm, "to": to, "requested_by": user, "at": time.time()},
          open(tmp, "w"))
os.replace(tmp, path)
PY
  chown 1001:0 "$UPD/status.json" 2>/dev/null || true
  echo "$(date -Is) [$1] $2"
}

installed_k9aif() {
  $PODMAN exec "$CONTAINER" python -c 'from importlib.metadata import version; print(version("k9-aif"))' 2>/dev/null || echo unknown
}
latest_k9aif() {
  curl -fsS --max-time 10 https://pypi.org/pypi/k9-aif/json \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null || echo unknown
}
healthy() {
  for _ in $(seq 1 45); do
    curl -fsS --max-time 3 "http://localhost:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

# Never leave "running" behind: the About page would keep the button disabled.
trap '[[ "$STATE" == running ]] && status failed "Updater stopped unexpectedly; see update.log"' EXIT

echo "$(date -Is) ---- update requested by $REQUESTED_BY"
status running "Checking for updates"

if ! git_ fetch --quiet origin; then status failed "git fetch failed; nothing changed"; exit 1; fi
UPSTREAM="$(git_ rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo origin/main)"
LOCAL="$(git_ rev-parse --short HEAD)"; REMOTE="$(git_ rev-parse --short "$UPSTREAM")"
INSTALLED="$(installed_k9aif)"; LATEST="$(latest_k9aif)"
echo "code $LOCAL (origin $REMOTE); k9-aif $INSTALLED (PyPI $LATEST)"

if [[ "$LOCAL" == "$REMOTE" && "$INSTALLED" != unknown && "$INSTALLED" == "$LATEST" ]]; then
  status up_to_date "Already up to date: build $LOCAL, k9-aif $INSTALLED" "$LOCAL" "$LOCAL"
  exit 0
fi

if [[ "$LOCAL" != "$REMOTE" ]]; then
  status running "Pulling $LOCAL -> $REMOTE"
  if ! git_ pull --ff-only --quiet; then
    status failed "git pull --ff-only failed (local changes on the host?); still running $LOCAL" "$LOCAL" "$REMOTE"
    exit 1
  fi
fi
NEW="$(git_ rev-parse --short HEAD)"

$PODMAN image exists "$IMAGE" && $PODMAN tag "$IMAGE" "$PREVIOUS"
status running "Building $NEW (k9-aif $LATEST)" "$LOCAL" "$NEW"
if ! "$SCRIPT_DIR/build-run.sh" build; then
  status failed "Build failed; still running the previous version ($LOCAL, k9-aif $INSTALLED)" "$LOCAL" "$NEW"
  exit 1
fi

status running "Restarting on $NEW" "$LOCAL" "$NEW"
"$SCRIPT_DIR/build-run.sh" start
if healthy; then
  NOW_K9="$(installed_k9aif)"
  status updated "Updated: build $LOCAL -> $NEW, k9-aif $INSTALLED -> $NOW_K9" "$LOCAL" "$NEW"
  exit 0
fi

echo "new build failed its health check; rolling back"
if $PODMAN image exists "$PREVIOUS"; then
  $PODMAN tag "$PREVIOUS" "$IMAGE"
  "$SCRIPT_DIR/build-run.sh" start
  if healthy; then
    status rolled_back "Build $NEW failed its health check; rolled back to the previous image ($LOCAL)" "$LOCAL" "$NEW"
  else
    status failed "Build $NEW unhealthy and the rollback did not come up; check: build-run.sh logs" "$LOCAL" "$NEW"
  fi
else
  status failed "Build $NEW unhealthy and there is no previous image to roll back to" "$LOCAL" "$NEW"
fi
exit 1
