#!/usr/bin/env bash
# K9X Inspector — build and run helper (single container).
#   build | start | stop | logs | all | update | install-updater | uninstall-updater
#
# build    bakes the commit and build time into the image (About, /api/health) and passes the latest k9-aif
#          on PyPI, so a rebuild installs a new framework release without --no-cache.
# update   the pull-based updater (ubuntu/update.sh): no-op when up to date; else git pull, build, restart,
#          health check, roll back to the previous image if unhealthy.
# install-updater  a systemd path unit that runs update.sh when the UI's Update button writes
#          runtime/update/request.json. Outbound only (GitHub, PyPI); nothing reaches into the host.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="k9x-inspector:latest"; CONTAINER="k9x-inspector"; PORT=8115
RUNTIME_HOST_DIR="${HOME}/containers/volumes/k9x-inspector/runtime"
ENV_FILE="$PROJECT_DIR/.env"
UNIT="k9x-inspector-update"

latest_k9aif() {
  curl -fsS --max-time 10 https://pypi.org/pypi/k9-aif/json 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null \
    || date -u +%Y%m%d        # PyPI unreachable: bust the pip layer once a day
}

case "${1:-help}" in
  build)
    cd "$PROJECT_DIR"
    COMMIT="$(git -c safe.directory="$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    LATEST="$(latest_k9aif)"
    echo "Building $IMAGE at $COMMIT (k9-aif latest: $LATEST)"
    sudo podman build -t "$IMAGE" -f ubuntu/Containerfile \
      --build-arg INSPECTOR_COMMIT="$COMMIT" \
      --build-arg INSPECTOR_BUILT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --build-arg K9AIF_LATEST="$LATEST" . ;;
  start)
    [[ -f "$ENV_FILE" ]] || { echo "Error: $ENV_FILE not found. Copy .env.example to .env."; exit 1; }
    sudo mkdir -p "$RUNTIME_HOST_DIR/update" && sudo chown -R 1001:0 "$RUNTIME_HOST_DIR"
    sudo podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
    sudo podman run -d --name "$CONTAINER" --restart=always -p "$PORT:8115" \
      --env-file "$ENV_FILE" -v "$RUNTIME_HOST_DIR:/app/runtime:Z" "$IMAGE"
    echo "K9X Inspector on port $PORT" ;;
  stop) sudo podman stop "$CONTAINER" ;;
  logs) sudo podman logs -f "$CONTAINER" ;;
  all) "$0" build && "$0" start ;;
  update) exec "$SCRIPT_DIR/update.sh" ;;
  install-updater)
    REQ="$RUNTIME_HOST_DIR/update/request.json"
    sudo mkdir -p "$RUNTIME_HOST_DIR/update"
    sudo tee "/etc/systemd/system/$UNIT.service" >/dev/null <<EOF
[Unit]
Description=K9X Inspector: pull-based update (git pull, rebuild with latest k9-aif, restart, health check)

[Service]
Type=oneshot
Environment=HOME=$HOME
ExecStart=$SCRIPT_DIR/update.sh
TimeoutStartSec=1800
EOF
    sudo tee "/etc/systemd/system/$UNIT.path" >/dev/null <<EOF
[Unit]
Description=K9X Inspector: run the updater when the UI asks (runtime/update/request.json)

[Path]
PathExists=$REQ
Unit=$UNIT.service

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT.path"
    printf '{"installed_at": %s, "unit": "%s"}\n' "$(date +%s)" "$UNIT" | sudo tee "$RUNTIME_HOST_DIR/update/updater.json" >/dev/null
    sudo chown -R 1001:0 "$RUNTIME_HOST_DIR/update" && sudo chmod -R g+rwX "$RUNTIME_HOST_DIR/update"
    echo "Updater installed: systemctl status $UNIT.path; log in $RUNTIME_HOST_DIR/update/update.log" ;;
  uninstall-updater)
    sudo systemctl disable --now "$UNIT.path" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$UNIT.path" "/etc/systemd/system/$UNIT.service" "$RUNTIME_HOST_DIR/update/updater.json"
    sudo systemctl daemon-reload
    echo "Updater removed" ;;
  *) echo "usage: $0 build|start|stop|logs|all|update|install-updater|uninstall-updater" ;;
esac
