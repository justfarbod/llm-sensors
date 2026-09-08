#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

ENV_FILE=.env.deploy
ENV_TEMPLATE=deployment.env.example
COMPOSE_FILE=compose.deploy.yaml

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker with the Compose plugin and try again." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is required (the 'docker compose' command)." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  echo "Created $ENV_FILE from $ENV_TEMPLATE."
fi

if ! grep -q '^WEBUI_SECRET_KEY=' "$ENV_FILE"; then
  printf '\nWEBUI_SECRET_KEY=\n' >> "$ENV_FILE"
fi

if grep -q '^WEBUI_SECRET_KEY=$' "$ENV_FILE"; then
  if command -v openssl >/dev/null 2>&1; then
    WEBUI_SECRET=$(openssl rand -hex 32)
  elif [ -r /dev/urandom ]; then
    WEBUI_SECRET=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  else
    echo "Cannot generate WEBUI_SECRET_KEY: install openssl and retry." >&2
    exit 1
  fi

  TEMP_ENV_FILE="${ENV_FILE}.tmp.$$"
  trap 'rm -f "$TEMP_ENV_FILE"' EXIT HUP INT TERM
  awk -v secret="$WEBUI_SECRET" '
    /^WEBUI_SECRET_KEY=$/ { print "WEBUI_SECRET_KEY=" secret; next }
    { print }
  ' "$ENV_FILE" > "$TEMP_ENV_FILE"
  chmod 600 "$TEMP_ENV_FILE"
  mv "$TEMP_ENV_FILE" "$ENV_FILE"
  trap - EXIT HUP INT TERM
  echo "Generated WEBUI_SECRET_KEY in $ENV_FILE."
fi

chmod 600 "$ENV_FILE"

echo "Pulling the private GitLab image. If this fails with 'unauthorized', run:"
echo "  docker login gitlab.uni-marburg.de:5050"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

OPEN_WEBUI_PORT_VALUE=$(sed -n 's/^OPEN_WEBUI_PORT=//p' "$ENV_FILE" | tail -n 1)
OPEN_WEBUI_PORT_VALUE=${OPEN_WEBUI_PORT_VALUE:-3000}
echo "Open WebUI is starting at http://localhost:${OPEN_WEBUI_PORT_VALUE}/"
