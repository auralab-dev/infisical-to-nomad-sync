#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

cleanup() {
  exit_code=$?
  trap - EXIT INT TERM
  docker compose \
    --file "$ROOT/integration/docker-compose.yml" \
    down --volumes --remove-orphans || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

docker compose \
  --file "$ROOT/integration/docker-compose.yml" \
  up --build --detach postgres redis infisical nomad

(cd "$ROOT" && python3 -m integration.e2e)
