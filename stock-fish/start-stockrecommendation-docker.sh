#!/usr/bin/env sh
set -eu
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env.docker.local"
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
command -v docker >/dev/null 2>&1 || { echo 'Docker CLI is unavailable.' >&2; exit 1; }
docker --version
docker compose version
docker info --format '{{.ServerVersion}}'
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  API_KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  [ "${#API_KEY}" -eq 64 ] || { echo 'Secure key generation failed.' >&2; exit 1; }
  { printf 'STOCKFISH_PORT=%s\n' "${STOCKFISH_PORT:-8000}";
    printf 'STOCKFISH_ALLOWED_HOSTS=localhost,127.0.0.1\n';
    printf 'STOCKFISH_API_KEY=%s\n' "$API_KEY"; } > "$ENV_FILE"
  unset API_KEY
fi
compose config --quiet
SERVICES="$(compose ps --services --status running)"
if printf '%s\n' "$SERVICES" | grep -qx stockfish; then
  BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-$PROJECT_ROOT/.docker-backups}"
  STAMP="$(date +%Y%m%d-%H%M%S)-$$"
  TARGET="$BACKUP_DIRECTORY/before-start-$STAMP"
  [ ! -e "$TARGET" ] || { echo 'Backup destination must be new.' >&2; exit 1; }
  mkdir -p "$BACKUP_DIRECTORY"
  INSIDE="/app/data/.backups/stockfish-backup-$STAMP"
  compose exec -T stockfish python scripts/backup_restore_state.py backup --database /app/data/stockfish.sqlite3 --artifact-root /app/data/core --destination "$INSIDE"
  compose cp "stockfish:$INSIDE" "$TARGET"
  printf 'Verified pre-start SQLite/WAL/artifact backup copied to %s\n' "$TARGET"
fi
if [ "${NO_BUILD:-0}" != 1 ]; then compose build stockfish; fi
EXPECTED_IMAGE="$(docker image inspect stockfish-core:local --format '{{.Id}}')"
compose up -d --wait --no-build stockfish
CONTAINER="$(compose ps -q stockfish)"
[ -n "$CONTAINER" ] || { echo 'No stockfish container.' >&2; exit 1; }
ACTUAL_IMAGE="$(docker inspect "$CONTAINER" --format '{{.Image}}')"
HEALTH="$(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}')"
[ "$ACTUAL_IMAGE" = "$EXPECTED_IMAGE" ] && [ "$HEALTH" = healthy ] || { echo 'Current image/health verification failed.' >&2; exit 1; }
printf 'This invocation verified image %s and container %s as healthy.\n' "$ACTUAL_IMAGE" "$CONTAINER"
printf 'Credentials remain in private %s.\n' "$ENV_FILE"
