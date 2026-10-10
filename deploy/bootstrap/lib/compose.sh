# shellcheck shell=bash
# Docker Compose orchestration for the deploy/compose stack. Provider
# independent: nothing here knows or cares which cloud (if any) the VM
# runs on.

readonly COMPOSE_PROJECT=mak4i
readonly DATA_VOLUMES="pgdata artifacts caddy_data caddy_config"

# compose ARGS... — the stack with the installation's profile: `tls` adds
# Caddy; `private-http` doesn't. PROFILE is set by the command.
compose() {
  if [[ "${PROFILE:-}" == tls ]]; then
    docker compose --project-directory "$COMPOSE_DIR" --file "$COMPOSE_DIR/compose.yaml" \
      --env-file "$ENV_FILE" --profile tls "$@"
  else
    docker compose --project-directory "$COMPOSE_DIR" --file "$COMPOSE_DIR/compose.yaml" \
      --env-file "$ENV_FILE" "$@"
  fi
}

# compose_all ARGS... — every service, whatever the profile (for ps/down).
compose_all() {
  docker compose --project-directory "$COMPOSE_DIR" --file "$COMPOSE_DIR/compose.yaml" \
    --env-file "$ENV_FILE" --profile tls "$@"
}

service_running() {
  compose_all ps --status running --services 2>/dev/null | grep -qx "$1"
}

volume_exists() {
  docker volume inspect "${COMPOSE_PROJECT}_$1" >/dev/null 2>&1
}

existing_volumes() {
  local v out=""
  for v in $DATA_VOLUMES; do
    if volume_exists "$v"; then out+="${COMPOSE_PROJECT}_$v "; fi
  done
  printf '%s' "${out% }"
}

# pinned_image SERVICE — the image reference compose.yaml pins for SERVICE.
pinned_image() {
  awk -v svc="  $1:" '$0 == svc { found = 1; next } found && $1 == "image:" { print $2; exit }' \
    "$COMPOSE_DIR/compose.yaml"
}

# http_get PATH — GET a path on the MAK4I server from inside its own
# container (no host tools or host port needed). Prints the body.
http_get() {
  compose exec -T mak4i python -c \
    "import sys, urllib.request; sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8080$1', timeout=5).read().decode())" \
    2>/dev/null
}

health_ok() { [[ "$(http_get /health)" == ok ]]; }
ready_ok() { [[ "$(http_get /ready)" == ready ]]; }

# db_counts — "organizations principals projects grants credentials".
db_counts() {
  # shellcheck disable=SC2016 # expanded by the container's shell
  compose exec -T postgres sh -c \
    'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select (select count(*) from organizations), (select count(*) from principals), (select count(*) from projects), (select count(*) from grants), (select count(*) from credentials)"' \
    2>/dev/null | tr '|' ' ' || true
}

artifact_count() {
  compose exec -T mak4i sh -c 'find /data/artifacts -type f -name "*.json" | wc -l' 2>/dev/null | tr -d ' \r' || true
}

migration_at_head() {
  compose exec -T mak4i alembic current 2>/dev/null | grep -q '(head)'
}

# running_version — the MAK4I release the running container reports, as a
# tag (v0.1.0-rc.5), or nothing.
running_version() {
  local v
  v=$(compose exec -T mak4i mak4i --version 2>/dev/null | awk '{print $2}') || return 0
  pep440_to_tag "$v" || true
}

pep440_to_tag() {
  printf '%s\n' "$1" | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)rc\([0-9][0-9]*\)$/v\1-rc.\2/p'
}

# collation_mismatch — databases whose recorded collation version differs
# from the running C library (e.g. after the PostgreSQL image moved to a
# newer Debian). Needs a REINDEX; see the Enterprise guide.
collation_mismatch() {
  # shellcheck disable=SC2016 # expanded by the container's shell
  compose exec -T postgres sh -c \
    'psql -X -q -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select datname from pg_database where datcollversion is not null and datcollversion <> pg_database_collation_actual_version(oid)"' \
    2>/dev/null || true
}

# wait_up TIMEOUT — start/converge the stack and wait until every service
# is healthy (migrate: exited 0). Migrations run as part of this.
wait_up() {
  run compose up -d --wait --wait-timeout "$1"
}

# verify_health — /health and /ready from inside the container.
verify_health() {
  [[ "$DRY_RUN" == 1 ]] && return 0
  health_ok || { err "/health did not return ok"; return 1; }
  ready_ok || { err "/ready did not return ready (control-plane database unreachable?)"; return 1; }
  info "/health: ok   /ready: ready"
}

# tls_ready DOMAIN [ISSUER] — HTTPS /ready through Caddy on this host,
# with the certificate verified: resolves DOMAIN to 127.0.0.1 so neither
# public DNS nor hairpin NAT is involved. For the internal issuer, trusts
# Caddy's local CA only (never disables verification).
tls_ready() {
  local domain=$1 issuer=${2:-acme} ca="" body
  if [[ "$issuer" == internal ]]; then
    ca=$(mktemp) || return 1
    if ! compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt >"$ca" 2>/dev/null; then
      rm -f -- "$ca"
      return 1
    fi
    body=$(curl -sS -m 10 --cacert "$ca" --resolve "$domain:443:127.0.0.1" "https://$domain/ready" 2>/dev/null) || body=""
    rm -f -- "$ca"
  else
    body=$(curl -sS -m 10 --resolve "$domain:443:127.0.0.1" "https://$domain/ready" 2>/dev/null) || body=""
  fi
  [[ "$body" == ready ]]
}

caddy_diagnostics() {
  info "Recent Caddy certificate messages:"
  compose logs --no-color --tail 200 caddy 2>/dev/null |
    grep -iE 'NXDOMAIN|timeout|challenge|could not get certificate|obtain|error' | tail -n 8 |
    sed 's/^/      /' || true
}
