#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="finally"

if docker ps -q --filter "name=^${CONTAINER_NAME}$" | grep -q .; then
  echo "Stopping FinAlly..."
  docker stop "$CONTAINER_NAME" >/dev/null
  docker rm "$CONTAINER_NAME" >/dev/null
  echo "Stopped. Data volume preserved."
elif docker ps -aq --filter "name=^${CONTAINER_NAME}$" | grep -q .; then
  echo "Removing stopped FinAlly container..."
  docker rm "$CONTAINER_NAME" >/dev/null
  echo "Removed. Data volume preserved."
else
  echo "FinAlly is not running."
fi
