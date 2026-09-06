#!/usr/bin/env sh
set -eu

PORT="${STOCKFISH_PORT:-8000}"
NO_BUILD="${NO_BUILD:-0}"
BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env.docker.local"

step() {
  printf '[StockFish Docker] %s\n' "$1"
}

new_api_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

env_value() {
  name="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  awk -F= -v key="$name" '$1 == key {print substr($0, length(key)+2); exit}' "$ENV_FILE"
}

command -v docker >/dev/null 2>&1 || {
  echo "Docker CLI was not found. Install Docker Engine/Desktop and retry." >&2
  exit 1
}
[ -f "$COMPOSE_FILE" ] || {
  echo "docker-compose.yml not found at $COMPOSE_FILE" >&2
  exit 1
}

step "$(docker --version)"
step "$(docker compose version)"
docker info >/dev/null 2>&1 || {
  echo "Docker daemon is not reachable. Start Docker and retry." >&2
  exit 1
}

if [ ! -f "$ENV_FILE" ]; then
  {
    printf 'STOCKFISH_PORT=%s\n' "$PORT"
    printf 'STOCKFISH_ALLOWED_HOSTS=localhost,127.0.0.1\n'
    printf 'STOCKFISH_API_KEY=%s\n' "$(new_api_key)"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE" || true
  step "Created private local env file: $ENV_FILE"
else
  step "Using existing private local env file: $ENV_FILE"
fi

EFFECTIVE_PORT="$(env_value STOCKFISH_PORT)"
[ -n "$EFFECTIVE_PORT" ] || EFFECTIVE_PORT="$PORT"
API_KEY="$(env_value STOCKFISH_API_KEY)"
[ -n "$API_KEY" ] || {
  echo "STOCKFISH_API_KEY is missing from $ENV_FILE" >&2
  exit 1
}

if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps --services --status running 2>/dev/null | grep -qx stockfish; then
  if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$EFFECTIVE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $EFFECTIVE_PORT is already in use. Stop the listener or edit STOCKFISH_PORT in $ENV_FILE." >&2
    exit 1
  fi
fi

if [ "$NO_BUILD" = "1" ]; then
  step "Running: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d --wait"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --wait
else
  step "Running: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d --wait --build"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --wait --build
fi

IMAGE_ID="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" images -q stockfish | tr -d '\n')"
step "Image ID: $IMAGE_ID"

HEALTH_URL="http://127.0.0.1:$EFFECTIVE_PORT/api/health"
i=0
while [ "$i" -lt 60 ]; do
  if python - "$HEALTH_URL" "$API_KEY" <<'PY'
import sys, urllib.request
url, token = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
urllib.request.urlopen(req, timeout=5).read()
PY
  then
    step "Health check passed: $HEALTH_URL"
    break
  fi
  i=$((i + 1))
  sleep 2
  if [ "$i" -eq 60 ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=120 stockfish
    echo "Container did not become healthy within the wait window." >&2
    exit 1
  fi
done

if [ -n "$BACKUP_DIRECTORY" ]; then
  mkdir -p "$BACKUP_DIRECTORY"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  TARGET="$BACKUP_DIRECTORY/stockfish-$STAMP.sqlite3"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" cp stockfish:/app/data/stockfish.sqlite3 "$TARGET"
  step "Database backup copied to: $TARGET"
fi

step "Open http://127.0.0.1:$EFFECTIVE_PORT/ . The API key is stored in $ENV_FILE and was not printed."
