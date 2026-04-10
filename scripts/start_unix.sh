#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="finally"
IMAGE_NAME="finally"
DATA_VOLUME="finally-data"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build image if it doesn't exist or --build was passed
if [ "${1:-}" = "--build" ] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "Building FinAlly image..."
  docker build -t "$IMAGE_NAME" "$PROJECT_ROOT"
fi

# Stop existing container if running
if docker ps -q --filter "name=^${CONTAINER_NAME}$" | grep -q .; then
  echo "Container already running. Stopping first..."
  docker stop "$CONTAINER_NAME" >/dev/null
  docker rm "$CONTAINER_NAME" >/dev/null
fi

# Remove stopped container with same name if it exists
docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true

# Run the container
docker run -d \
  --name "$CONTAINER_NAME" \
  -v "${DATA_VOLUME}:/app/db" \
  -p 8000:8000 \
  --env-file "$PROJECT_ROOT/.env" \
  "$IMAGE_NAME" >/dev/null

echo ""
echo "FinAlly is running at http://localhost:8000"
echo "Stop with: ./scripts/stop_unix.sh"
