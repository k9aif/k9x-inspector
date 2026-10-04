#!/usr/bin/env bash
# K9X Inspector — build and run helper (single container).
#   build | start | stop | logs | all
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="k9x-inspector:latest"; CONTAINER="k9x-inspector"; PORT=8115
RUNTIME_HOST_DIR="${HOME}/containers/volumes/k9x-inspector/runtime"
ENV_FILE="$PROJECT_DIR/.env"
case "${1:-help}" in
  build) cd "$PROJECT_DIR" && sudo podman build -t "$IMAGE" -f ubuntu/Containerfile . ;;
  start)
    [[ -f "$ENV_FILE" ]] || { echo "Error: $ENV_FILE not found. Copy .env.example to .env."; exit 1; }
    sudo mkdir -p "$RUNTIME_HOST_DIR" && sudo chown -R 1001:0 "$RUNTIME_HOST_DIR"
    sudo podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
    sudo podman run -d --name "$CONTAINER" --restart=always -p "$PORT:8115" \
      --env-file "$ENV_FILE" -v "$RUNTIME_HOST_DIR:/app/runtime:Z" "$IMAGE"
    echo "K9X Inspector on port $PORT" ;;
  stop) sudo podman stop "$CONTAINER" ;;
  logs) sudo podman logs -f "$CONTAINER" ;;
  all) "$0" build && "$0" start ;;
  *) echo "usage: $0 build|start|stop|logs|all" ;;
esac
