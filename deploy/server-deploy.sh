#!/usr/bin/env bash
# Deploys Elder Companion on the server. Run from /opt/elder as:
#   IMAGE=<ecr-uri>:<sha> SITE_HOST=<host> AWS_REGION=<region> bash server-deploy.sh
set -euo pipefail

# bash 5.2+: stop "&" in a ${var//x/y} replacement from meaning "the matched text"
shopt -u patsub_replacement 2>/dev/null || true

APP_VARS=(LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET RESEND_API_KEY EMAIL_FROM)

# render_caddyfile HOST USER HASH -> Caddyfile on stdout (literal substitution)
render_caddyfile() {
  local out
  out="$(<Caddyfile.template)"
  out="${out//__SITE_HOST__/$1}"
  out="${out//__SITE_USER__/$2}"
  out="${out//__SITE_HASH__/$3}"
  printf '%s\n' "$out"
}

# write_env: "/elder/NAME<TAB>VALUE" lines on stdin -> "NAME=VALUE" lines on stdout.
# Leaves out the SITE_* values and fails if an app variable is missing or empty.
# Also records IMAGE, so later manual `docker compose ...` commands in /opt/elder work.
write_env() {
  local path value name lines="" seen=" "
  while IFS=$'\t' read -r path value || [[ -n "$path" ]]; do
    name="${path##*/}"
    case "$name" in SITE_*|"") continue ;; esac
    [[ -n "$value" ]] || continue
    lines+="$name=$value"$'\n'
    seen+="$name "
  done
  for name in "${APP_VARS[@]}"; do
    if [[ "$seen" != *" $name "* ]]; then
      echo "missing: $name" >&2
      return 1
    fi
  done
  printf '%s' "$lines"
  echo "DB_PATH=/data/companion.db"
  if [[ -n "${IMAGE:-}" ]]; then echo "IMAGE=$IMAGE"; fi
}

# param_value NAME < parameters -> value of /elder/NAME
param_value() {
  awk -F'\t' -v want="/elder/$1" '$1 == want { sub(/^[^\t]*\t/, ""); print; exit }'
}

main() {
  : "${IMAGE:?}" "${SITE_HOST:?}" "${AWS_REGION:?}"
  export HOME="${HOME:-/root}" AWS_REGION IMAGE
  cd "$(dirname "$0")"

  local params user pw hash
  params="$(aws ssm get-parameters-by-path --path /elder/ --with-decryption \
    --query 'Parameters[*].[Name,Value]' --output text)"

  (umask 077 && write_env <<<"$params" > .env.tmp && mv .env.tmp .env)

  user="$(param_value SITE_USER <<<"$params")"
  pw="$(param_value SITE_PASSWORD <<<"$params")"
  [[ -n "$user" ]] || { echo "missing: SITE_USER" >&2; exit 1; }
  [[ -n "$pw" ]] || { echo "missing: SITE_PASSWORD" >&2; exit 1; }
  hash="$(docker run --rm caddy:2.10 caddy hash-password --plaintext "$pw")"
  render_caddyfile "$SITE_HOST" "$user" "$hash" > Caddyfile

  aws ecr get-login-password | docker login --username AWS --password-stdin "${IMAGE%%/*}"
  docker compose pull
  docker compose up -d --remove-orphans
  docker image prune -f

  local i running
  for i in $(seq 1 12); do
    running="$(docker compose ps --status running --services | wc -l)"
    if [[ "$running" -eq 3 ]]; then
      echo "all 3 services running"
      return 0
    fi
    sleep 5
  done
  docker compose ps
  docker compose logs --tail 50
  exit 1
}

if [[ "${SOURCE_ONLY:-}" != 1 ]]; then
  main "$@"
fi
