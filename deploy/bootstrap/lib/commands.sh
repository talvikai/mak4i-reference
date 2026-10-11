# shellcheck shell=bash
# The bootstrap's lifecycle commands. Each cmd_* is called by main() after
# option parsing; state lives in the Compose .env (MAK4I_BOOTSTRAP_PROFILE,
# MAK4I_INSTALL_STATE, MAK4I_INSTALLED_RELEASE) and in the Docker volumes.

readonly UPGRADE_FROM="v0.1.0-rc.4"

compose_hint() {
  if [[ "$PROFILE" == tls ]]; then printf 'docker compose --profile tls'; else printf 'docker compose'; fi
}

# load_state — what's already on this host.
#   EXIST_STATE: none | unmanaged | pending | complete | preserved
load_state() {
  STORED_PROFILE="" STORED_DOMAIN="" STORED_RELEASE=""
  if [[ ! -f "$ENV_FILE" ]]; then
    EXIST_STATE=none
    return
  fi
  [[ ! -L "$ENV_FILE" ]] || die "$EX_FAILURE" "$ENV_FILE is a symlink; refusing to use it"
  EXIST_STATE=$(env_get "$ENV_FILE" MAK4I_INSTALL_STATE)
  [[ -n "$EXIST_STATE" ]] || EXIST_STATE=unmanaged
  STORED_PROFILE=$(env_get "$ENV_FILE" MAK4I_BOOTSTRAP_PROFILE)
  STORED_DOMAIN=$(env_get "$ENV_FILE" MAK4I_DOMAIN)
  STORED_RELEASE=$(env_get "$ENV_FILE" MAK4I_INSTALLED_RELEASE)
}

# require_installed — for commands that operate on an existing deployment.
require_installed() {
  load_state
  case "$EXIST_STATE" in
    none) die "$EX_FAILURE" "no MAK4I installation found ($ENV_FILE doesn't exist). Run: $0 install --help" ;;
    unmanaged) die "$EX_EXISTING" "this installation was set up manually and isn't managed by the bootstrap yet. Adopt it with: $0 upgrade" ;;
  esac
  PROFILE=${PROFILE:-$STORED_PROFILE}
  DOMAIN=${DOMAIN:-$STORED_DOMAIN}
  [[ -n "$PROFILE" ]] || die "$EX_FAILURE" "MAK4I_BOOTSTRAP_PROFILE is missing from $ENV_FILE"
  HTTP_PORT=$(env_get "$ENV_FILE" MAK4I_HTTP_PORT)
  HTTP_PORT=${HTTP_PORT:-8080}
  TLS_ISSUER=$(env_get "$ENV_FILE" MAK4I_TLS_ISSUER)
  TLS_ISSUER=${TLS_ISSUER:-acme}
}

# --- preflight --------------------------------------------------------------

cmd_preflight() {
  load_state
  PROFILE=${PROFILE:-$STORED_PROFILE}
  DOMAIN=${DOMAIN:-$STORED_DOMAIN}
  if [[ -z "$PROFILE" ]]; then
    if [[ -n "$DOMAIN" ]]; then PROFILE=tls; else usage_error "pass --profile tls (with --domain) or --profile private-http"; fi
  fi
  preflight || exit "$EX_PREFLIGHT"
}

# --- install ------------------------------------------------------------------

_install_settings() {
  if [[ -n "$STORED_PROFILE" && -n "$PROFILE" && "$PROFILE" != "$STORED_PROFILE" ]]; then
    die "$EX_EXISTING" "an installation with profile '$STORED_PROFILE' already exists; install won't change it to '$PROFILE'. Keep the profile, or uninstall first"
  fi
  if [[ -n "$STORED_DOMAIN" && -n "$DOMAIN" && "$DOMAIN" != "$STORED_DOMAIN" && "${STORED_PROFILE:-}" == tls ]]; then
    die "$EX_EXISTING" "an installation for domain '$STORED_DOMAIN' already exists; install won't change it to '$DOMAIN'"
  fi
  PROFILE=${PROFILE:-$STORED_PROFILE}
  DOMAIN=${DOMAIN:-$STORED_DOMAIN}
  [[ -n "$PROFILE" ]] || usage_error "install needs --profile tls (with --domain) or --profile private-http"
  if [[ "$PROFILE" == tls ]]; then
    [[ -n "$DOMAIN" ]] || usage_error "--profile tls needs --domain"
    BIND=127.0.0.1
    ENDPOINT=${ENDPOINT:-https://$DOMAIN/mcp}
  else
    ENDPOINT=${ENDPOINT:-http://$BIND:$HTTP_PORT/mcp}
  fi
  if [[ "$EXIST_STATE" != none ]]; then
    # Converging an existing managed installation: its saved values win.
    HTTP_PORT=$(env_get "$ENV_FILE" MAK4I_HTTP_PORT)
    HTTP_PORT=${HTTP_PORT:-8080}
    BIND=$(env_get "$ENV_FILE" MAK4I_HTTP_BIND)
    BIND=${BIND:-127.0.0.1}
    TLS_ISSUER=$(env_get "$ENV_FILE" MAK4I_TLS_ISSUER)
    TLS_ISSUER=${TLS_ISSUER:-acme}
    ENDPOINT=$(env_get "$ENV_FILE" MAK4I_PUBLIC_ENDPOINT)
  fi
}

_write_new_env() {
  local secret
  secret=$(new_secret)
  [[ ${#secret} -eq 64 ]] || die "$EX_FAILURE" "could not generate a random database password"
  if [[ "$DRY_RUN" == 1 ]]; then
    info "[dry-run] would write $ENV_FILE (mode 600) with a new random database password"
    return
  fi
  MAK4I_BOOTSTRAP_SECRET=$secret
  export MAK4I_BOOTSTRAP_SECRET
  env_write "$ENV_FILE" "$COMPOSE_DIR/.env.example" \
    "POSTGRES_PASSWORD=@secret" \
    "MAK4I_PUBLIC_ENDPOINT=$ENDPOINT" \
    "MAK4I_DOMAIN=$([[ "$PROFILE" == tls ]] && printf '%s' "$DOMAIN")" \
    "MAK4I_TLS_ISSUER=$TLS_ISSUER" \
    "MAK4I_HTTP_BIND=$BIND" \
    "MAK4I_HTTP_PORT=$HTTP_PORT" \
    "MAK4I_INSTANCE_NAME=$INSTANCE_NAME" \
    "MAK4I_ENVIRONMENT=$ENVIRONMENT_LABEL" \
    "MAK4I_BOOTSTRAP_PROFILE=$PROFILE" \
    "MAK4I_INSTALL_STATE=pending" \
    "MAK4I_INSTALLED_RELEASE=$MAK4I_RELEASE"
  unset MAK4I_BOOTSTRAP_SECRET
  secret=""
  info "wrote $ENV_FILE (mode 600; database password generated, not shown)"
}

_install_failed() {
  local code=$1
  shift
  err "$*"
  cat >&2 <<EOF

The installation is INCOMPLETE (state: pending). Its configuration is kept in
$ENV_FILE, and any data volumes are kept.
  - Inspect:  $0 status   and   cd $COMPOSE_DIR && $(compose_hint) logs
  - Resume:   fix the problem, then re-run the same install command
  - Start over (deletes this installation's data): $0 uninstall --destroy-data
EOF
  [[ -z "$LOG_FILE" ]] || printf '  - Log:      %s\n' "$LOG_FILE" >&2
  exit "$code"
}

_wait_for_certificate() {
  local deadline=$((SECONDS + CERT_TIMEOUT))
  info "waiting up to ${CERT_TIMEOUT}s for Caddy to obtain the certificate for $DOMAIN ..."
  while ((SECONDS < deadline)); do
    if tls_ready "$DOMAIN" "$TLS_ISSUER"; then
      info "https://$DOMAIN/ready: ready (certificate verified)"
      return 0
    fi
    sleep 5
  done
  return 1
}

_certificate_pending() {
  err "no valid certificate for $DOMAIN after ${CERT_TIMEOUT}s"
  caddy_diagnostics
  cat >&2 <<EOF

MAK4I itself is running (health and readiness passed); only HTTPS is not
ready yet. Caddy keeps retrying automatically. The usual causes:
  - NXDOMAIN in the messages above: the A record for $DOMAIN doesn't exist
    or hasn't propagated yet. Wait, then check: dig +short $DOMAIN
  - timeout / connection errors: inbound TCP 80 and 443 are blocked by the
    cloud or host firewall (on GCP, allow HTTP and HTTPS traffic to the VM).
  - A CDN proxy in front of the name: keep it DNS-only until the first
    certificate has been issued.
Check again at any time with: $0 status
EOF
  env_set "$ENV_FILE" "MAK4I_INSTALL_STATE=complete" "MAK4I_INSTALLED_RELEASE=$MAK4I_RELEASE"
  exit "$EX_UNHEALTHY"
}

_next_steps() {
  local hint
  hint=$(compose_hint)
  cat <<EOF

MAK4I Enterprise $MAK4I_RELEASE is installed and healthy.
  Profile:   $PROFILE
  Endpoint:  $ENDPOINT
  Instance:  $(env_get "$ENV_FILE" MAK4I_INSTANCE_NAME) ($(env_get "$ENV_FILE" MAK4I_ENVIRONMENT))
  Config:    $ENV_FILE (mode 600; back it up with: $0 backup)

Next: create your organization, a project, a grant and a client credential
(docs/ENTERPRISE_SELF_HOSTED.md, "Create your organization"):
  cd $COMPOSE_DIR
  $hint exec mak4i mak4i org create --name "<Organization>" --owner-display-name "<Your name>"

Day-2 commands: $0 status | restart | backup | upgrade | uninstall
EOF
}

cmd_install() {
  load_state
  case "$EXIST_STATE" in
    unmanaged)
      die "$EX_EXISTING" "a manually installed MAK4I (.env without bootstrap state) exists in $COMPOSE_DIR. Adopt it with: $0 upgrade" ;;
    none)
      local vols=""
      if command -v docker >/dev/null 2>&1; then vols=$(existing_volumes); fi
      [[ -z "$vols" ]] || die "$EX_EXISTING" "MAK4I data volumes already exist ($vols) but $ENV_FILE doesn't. Restore the saved .env (a bootstrap backup contains it as 'env'), or remove the data with: $0 uninstall --destroy-data"
      ;;
  esac
  _install_settings

  step "Preflight"
  preflight || die "$EX_PREFLIGHT" "preflight failed; fix the FAIL items above. Nothing was changed."

  step "Configuration"
  if [[ "$EXIST_STATE" == none ]]; then
    _write_new_env
  else
    info "using the existing configuration in $ENV_FILE (state: $EXIST_STATE); no secrets are regenerated"
    [[ "$DRY_RUN" == 1 ]] || env_mode_ok "$ENV_FILE" || { chmod 600 "$ENV_FILE"; info "tightened $ENV_FILE to mode 600"; }
  fi
  if [[ "$DRY_RUN" != 1 ]]; then
    compose config -q || _install_failed "$EX_FAILURE" "the Compose configuration is invalid"
    info "Compose configuration valid"
  fi

  step "Images (pinned versions)"
  if [[ "$PROFILE" == tls ]]; then
    run compose pull postgres caddy || _install_failed "$EX_FAILURE" "could not pull the pinned images"
  else
    run compose pull postgres || _install_failed "$EX_FAILURE" "could not pull the pinned images"
  fi
  run compose build mak4i || _install_failed "$EX_FAILURE" "could not build the MAK4I image"

  step "Start (database migrations run first)"
  wait_up "$TIMEOUT" || _install_failed "$EX_UNHEALTHY" "the stack did not become healthy within ${TIMEOUT}s"

  step "Verify"
  verify_health || _install_failed "$EX_UNHEALTHY" "MAK4I is not healthy"
  if [[ "$DRY_RUN" != 1 ]]; then
    migration_at_head || _install_failed "$EX_FAILURE" "the control-plane schema is not at the latest migration"
    info "database schema: at the latest migration"
  fi

  if [[ "$PROFILE" == tls ]]; then
    step "HTTPS"
    if [[ "$DRY_RUN" != 1 ]]; then
      _wait_for_certificate || _certificate_pending
    fi
  fi

  if [[ "$DRY_RUN" == 1 ]]; then
    say ""
    say "Dry run complete: nothing was changed."
    return
  fi
  env_set "$ENV_FILE" "MAK4I_INSTALL_STATE=complete" "MAK4I_INSTALLED_RELEASE=$MAK4I_RELEASE"
  _next_steps
}

# --- status -----------------------------------------------------------------

# cmd_admin_info — issue #21: recover organization/owner/project/principal
# ids and credential metadata without querying the database by hand. Runs
# `mak4i admin inventory` inside the mak4i container (instance-operator
# trust, the same as this script's own database access). Never prints a
# token, hash, sign-in code or client secret.
cmd_admin_info() {
  require_installed
  service_running mak4i || die "$EX_UNHEALTHY" "the mak4i service is not running. Start it with: $0 restart"
  compose exec -T mak4i mak4i admin inventory || die "$EX_FAILURE" "could not read the administrative inventory"
}

cmd_status() {
  require_installed
  local problems=0 running counts vols
  say "MAK4I Enterprise status ($ENV_FILE)"
  info "Configured release: ${STORED_RELEASE:-unknown}   state: $EXIST_STATE"
  info "Profile: $PROFILE   Endpoint: $(env_get "$ENV_FILE" MAK4I_PUBLIC_ENDPOINT)"
  info "Instance: $(env_get "$ENV_FILE" MAK4I_INSTANCE_NAME) ($(env_get "$ENV_FILE" MAK4I_ENVIRONMENT))"
  say ""
  compose_all ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Status}}' 2>/dev/null | sed 's/^/    /'
  say ""
  vols=$(existing_volumes)
  info "Data volumes: ${vols:-none}"

  if ! service_running mak4i; then
    err "the mak4i service is not running. Start it with: $0 restart"
    exit "$EX_UNHEALTHY"
  fi
  running=$(running_version)
  info "Running version: ${running:-unknown}"
  if health_ok; then info "/health: ok"; else err "/health failed"; problems=1; fi
  if ready_ok; then info "/ready: ready"; else err "/ready failed: the control-plane database is unreachable (check: $0 status, docker compose logs postgres)"; problems=1; fi
  if migration_at_head; then info "Database schema: at the latest migration"; else warn "database schema is not at the latest migration"; problems=1; fi
  counts=$(db_counts)
  if [[ -n "$counts" ]]; then
    read -r c_org c_prn c_prj c_grant c_cred <<<"$counts"
    info "Control plane: $c_org organization(s), $c_prn principal(s), $c_prj project(s), $c_grant grant(s), $c_cred credential(s)"
  fi
  info "Artifacts: $(artifact_count) file(s)"
  if [[ "$PROFILE" == tls ]]; then
    if tls_ready "$DOMAIN" "$TLS_ISSUER"; then
      info "HTTPS: https://$DOMAIN/ready -> ready (certificate verified)"
    else
      err "HTTPS: https://$DOMAIN/ready is not ready"
      caddy_diagnostics
      problems=1
    fi
  fi
  if [[ "$problems" == 1 ]]; then
    exit "$EX_UNHEALTHY"
  fi
  say ""
  say "Healthy."
}

# --- restart ------------------------------------------------------------------

cmd_restart() {
  require_installed
  local before="" after
  if service_running postgres; then before=$(db_counts); fi
  step "Restart"
  run compose restart || die "$EX_FAILURE" "restart failed"
  wait_up "$TIMEOUT" || die "$EX_UNHEALTHY" "the stack did not become healthy within ${TIMEOUT}s after restart"
  step "Verify"
  verify_health || die "$EX_UNHEALTHY" "MAK4I is not healthy after restart"
  [[ "$DRY_RUN" == 1 ]] && { say "Dry run complete: nothing was changed."; return; }
  after=$(db_counts)
  if [[ -n "$before" && "$before" != "$after" ]]; then
    die "$EX_FAILURE" "control-plane counts changed across the restart (before: $before, after: $after)"
  fi
  info "data preserved (organizations principals projects grants credentials: $after)"
  if [[ "$PROFILE" == tls ]]; then
    local deadline=$((SECONDS + 60))
    until tls_ready "$DOMAIN" "$TLS_ISSUER"; do
      ((SECONDS < deadline)) || { warn "HTTPS not ready yet; check with: $0 status"; return 0; }
      sleep 3
    done
    info "HTTPS ready"
  fi
  say "Restarted and healthy."
}

# --- backup -------------------------------------------------------------------

_BACKUP_STOPPED=0
_backup_restart_on_exit() {
  if [[ "$_BACKUP_STOPPED" == 1 ]]; then
    warn "restarting MAK4I after an interrupted backup"
    compose start mak4i >/dev/null 2>&1 || true
  fi
}

# do_backup — take a verified backup; sets BACKUP_DEST to its directory.
BACKUP_DEST=""
do_backup() {
  local root dest counts acount host_count dump_sha list_sha
  root=$(safe_backup_root "${BACKUP_DIR:-$HOME/mak4i-backups}") || exit $?
  dest="$root/mak4i-backup-$(utc_stamp)"
  # Two backups in the same second (e.g. an upgrade retried immediately)
  # must not collide; suffix the name rather than refuse.
  local base=$dest n=1
  while [[ -e "$dest" ]]; do dest="$base-$n"; n=$((n + 1)); done
  service_running postgres || die "$EX_BACKUP" "PostgreSQL isn't running; start the stack first ($0 restart)"
  service_running mak4i || die "$EX_BACKUP" "MAK4I isn't running; start the stack first ($0 restart)"
  counts=$(db_counts)
  acount=$(artifact_count)
  [[ -n "$counts" && -n "$acount" ]] || die "$EX_BACKUP" "could not read the current data counts"

  step "Backup to $dest"
  if [[ "$DRY_RUN" == 1 ]]; then
    info "[dry-run] would stop mak4i (unless --online), dump PostgreSQL, copy the artifacts volume and .env, write checksums and a manifest"
    BACKUP_DEST=$dest
    return 0
  fi
  (umask 077 && mkdir -p -- "$dest") || die "$EX_BACKUP" "cannot create $dest"
  chmod 700 "$dest"

  if [[ "$ONLINE" != 1 ]]; then
    info "stopping mak4i for a consistent copy (clients see a brief outage)"
    trap _backup_restart_on_exit EXIT
    _BACKUP_STOPPED=1
    compose stop mak4i >/dev/null || die "$EX_BACKUP" "could not stop mak4i"
  fi

  # shellcheck disable=SC2016 # expanded by the container's shell

  compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/mak4i-backup.dump && pg_restore --list /tmp/mak4i-backup.dump >/dev/null' ||
    die "$EX_BACKUP" "pg_dump failed or produced an unreadable dump"
  compose cp postgres:/tmp/mak4i-backup.dump "$dest/control-plane.dump" >/dev/null ||
    die "$EX_BACKUP" "could not copy the database dump out"
  compose exec -T postgres rm -f /tmp/mak4i-backup.dump || true
  compose cp mak4i:/data/artifacts "$dest/artifacts" >/dev/null ||
    die "$EX_BACKUP" "could not copy the artifacts volume out"

  if [[ "$_BACKUP_STOPPED" == 1 ]]; then
    compose start mak4i >/dev/null || die "$EX_BACKUP" "could not start mak4i again"
    _BACKUP_STOPPED=0
    trap - EXIT
    wait_up "$TIMEOUT" >/dev/null || warn "mak4i did not report healthy within ${TIMEOUT}s; check: $0 status"
  fi

  cp -- "$ENV_FILE" "$dest/env"
  (
    cd -- "$dest" || exit 1
    find artifacts -type f | LC_ALL=C sort | while IFS= read -r f; do
      printf '%s  %s\n' "$(sha256_file "$f")" "$f"
    done >artifacts.sha256
  ) || die "$EX_BACKUP" "could not checksum the artifacts"

  host_count=$(find "$dest/artifacts" -type f -name '*.json' | wc -l | tr -d ' ')
  [[ "$host_count" == "$acount" ]] || die "$EX_BACKUP" "artifact count mismatch (volume: $acount, backup: $host_count)"
  [[ -s "$dest/control-plane.dump" ]] || die "$EX_BACKUP" "the database dump is empty"
  dump_sha=$(sha256_file "$dest/control-plane.dump")
  list_sha=$(sha256_file "$dest/artifacts.sha256")
  cat >"$dest/manifest.env" <<EOF
# MAK4I Enterprise backup manifest (written by deploy/bootstrap/mak4i-enterprise)
MAK4I_BACKUP_FORMAT=1
MAK4I_BACKUP_RELEASE=$MAK4I_RELEASE
MAK4I_BACKUP_CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MAK4I_BACKUP_PROFILE=$PROFILE
MAK4I_BACKUP_DB_COUNTS=$counts
MAK4I_BACKUP_ARTIFACT_COUNT=$acount
MAK4I_BACKUP_DUMP_SHA256=$dump_sha
MAK4I_BACKUP_ARTIFACTS_SHA256=$list_sha
EOF
  chmod -R go-rwx "$dest"
  info "control plane: $counts (organizations principals projects grants credentials)"
  info "artifacts:     $acount file(s)"
  info "verified: dump readable, artifact count matches, checksums written"
  info "NOTE: $dest/env contains the database password. Keep the backup private."
  BACKUP_DEST=$dest
}

cmd_backup() {
  require_installed
  do_backup
  [[ "$DRY_RUN" == 1 ]] || say "Backup complete: $BACKUP_DEST"
}

# --- restore ------------------------------------------------------------------

# verify_backup DIR — manifest present, checksums match, release compatible.
verify_backup() {
  local src=$1 manifest release dump_sha list_sha
  manifest="$src/manifest.env"
  [[ -f "$manifest" && -f "$src/control-plane.dump" && -d "$src/artifacts" && -f "$src/artifacts.sha256" ]] ||
    die "$EX_BACKUP" "$src is not a complete bootstrap backup (manifest.env, control-plane.dump, artifacts/, artifacts.sha256)"
  release=$(env_get "$manifest" MAK4I_BACKUP_RELEASE)
  # Backups from the previous release are accepted too: restore and upgrade
  # run migrations before MAK4I starts, so an older schema is brought up to
  # date, and a backup taken just before upgrading must stay usable.
  case "$release" in
    "$MAK4I_RELEASE" | "$UPGRADE_FROM") ;;
    *) die "$EX_UNSUPPORTED" "backup was made by $release; this bootstrap restores $MAK4I_RELEASE and $UPGRADE_FROM backups" ;;
  esac
  dump_sha=$(env_get "$manifest" MAK4I_BACKUP_DUMP_SHA256)
  list_sha=$(env_get "$manifest" MAK4I_BACKUP_ARTIFACTS_SHA256)
  [[ "$(sha256_file "$src/control-plane.dump")" == "$dump_sha" ]] || die "$EX_BACKUP" "control-plane.dump checksum mismatch"
  [[ "$(sha256_file "$src/artifacts.sha256")" == "$list_sha" ]] || die "$EX_BACKUP" "artifacts.sha256 checksum mismatch"
  (
    cd -- "$src" || exit 1
    while IFS= read -r line; do
      sum=${line%%  *}
      file=${line#*  }
      [[ "$(sha256_file "$file")" == "$sum" ]] || { echo "$file"; exit 1; }
    done <artifacts.sha256
  ) >/dev/null || die "$EX_BACKUP" "an artifact file doesn't match its checksum"
  info "backup verified: $(env_get "$manifest" MAK4I_BACKUP_CREATED_AT), $(env_get "$manifest" MAK4I_BACKUP_ARTIFACT_COUNT) artifact file(s), checksums match"
}

cmd_restore() {
  require_installed
  [[ -n "$RESTORE_FROM" ]] || usage_error "restore needs --from <backup directory>"
  local src counts acount
  src=$(abs_path "$RESTORE_FROM") || die "$EX_BACKUP" "no such backup: $RESTORE_FROM"
  step "Verify backup $src"
  verify_backup "$src"
  cat <<EOF

Restore REPLACES all current data of this installation with the backup:
  - control-plane database (organizations, principals, projects, grants,
    credentials): current contents are dropped and replaced;
  - artifacts volume: current files are deleted and replaced.
Credentials issued after the backup stop working. MAK4I is unavailable to
clients during the restore (typically well under a minute for small
installations). The current .env (database password) is kept.
EOF
  if [[ "$YES" != 1 ]]; then
    confirm_phrase "restore" "Take a fresh backup first if you may need the current data."
  fi
  service_running postgres || die "$EX_FAILURE" "PostgreSQL isn't running; run $0 restart first"

  step "Restore"
  run compose stop mak4i || die "$EX_FAILURE" "could not stop mak4i"
  run compose cp "$src/control-plane.dump" postgres:/tmp/mak4i-restore.dump || die "$EX_BACKUP" "could not copy the dump in"
  # shellcheck disable=SC2016 # expanded by the container's shell
  run compose exec -T postgres sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error /tmp/mak4i-restore.dump; rc=$?; rm -f /tmp/mak4i-restore.dump; exit $rc' ||
    die "$EX_BACKUP" "pg_restore failed; the database may be partially restored. Re-run the restore"
  run compose run --rm --no-deps --entrypoint find mak4i /data/artifacts -mindepth 1 -delete ||
    die "$EX_BACKUP" "could not clear the artifacts volume"
  run compose cp "$src/artifacts/." mak4i:/data/artifacts/ || die "$EX_BACKUP" "could not copy the artifacts in"
  # Backups are private (mode 700 directories owned by the host user), so
  # the one-off root chown also needs DAC_READ_SEARCH to descend into them.
  run compose run --rm --no-deps -u root --cap-add CHOWN --cap-add DAC_READ_SEARCH --entrypoint chown mak4i -R mak4i:mak4i /data/artifacts ||
    die "$EX_BACKUP" "could not re-own the restored artifacts"
  run compose run --rm migrate || die "$EX_FAILURE" "database migration after restore failed"
  wait_up "$TIMEOUT" || die "$EX_UNHEALTHY" "the stack did not become healthy after the restore"
  verify_health || die "$EX_UNHEALTHY" "MAK4I is not healthy after the restore"
  [[ "$DRY_RUN" == 1 ]] && { say "Dry run complete: nothing was changed."; return; }
  counts=$(db_counts)
  acount=$(artifact_count)
  [[ "$counts" == "$(env_get "$src/manifest.env" MAK4I_BACKUP_DB_COUNTS)" ]] ||
    die "$EX_BACKUP" "restored control-plane counts ($counts) don't match the backup"
  [[ "$acount" == "$(env_get "$src/manifest.env" MAK4I_BACKUP_ARTIFACT_COUNT)" ]] ||
    die "$EX_BACKUP" "restored artifact count ($acount) doesn't match the backup"
  say "Restore complete and verified: $counts (organizations principals projects grants credentials), $acount artifact file(s)."
}

# --- upgrade ------------------------------------------------------------------

cmd_upgrade() {
  load_state
  [[ "$EXIST_STATE" != none ]] || die "$EX_FAILURE" "no installation found ($ENV_FILE doesn't exist). Use: $0 install"
  local from before after abefore aafter checkout backup_dir mism
  if [[ "$EXIST_STATE" == unmanaged ]]; then
    PROFILE=${PROFILE:-}
    if [[ -z "$PROFILE" ]]; then
      if volume_exists caddy_data || service_running caddy; then PROFILE=tls; else PROFILE=private-http; fi
    fi
    info "adopting a manually installed deployment (profile: $PROFILE)"
  else
    PROFILE=${PROFILE:-$STORED_PROFILE}
  fi
  DOMAIN=$(env_get "$ENV_FILE" MAK4I_DOMAIN)
  TLS_ISSUER=$(env_get "$ENV_FILE" MAK4I_TLS_ISSUER)
  TLS_ISSUER=${TLS_ISSUER:-acme}

  from=${STORED_RELEASE:-}
  if [[ -z "$from" ]] && service_running mak4i; then from=$(running_version); fi
  from=${from:-$FROM_VERSION}
  [[ -n "$from" ]] || die "$EX_UNSUPPORTED" "can't determine the installed version: start the stack, or pass --from-version $UPGRADE_FROM"
  if [[ -n "$FROM_VERSION" && "$FROM_VERSION" != "$from" ]]; then
    die "$EX_UNSUPPORTED" "--from-version $FROM_VERSION doesn't match the installed version $from"
  fi
  case "$from" in
    "$UPGRADE_FROM") info "installed: $from -> upgrading to $MAK4I_RELEASE" ;;
    "$MAK4I_RELEASE") info "already $MAK4I_RELEASE; re-applying and verifying" ;;
    *) die "$EX_UNSUPPORTED" "upgrading from $from isn't supported. Supported path: $UPGRADE_FROM -> $MAK4I_RELEASE (upgrade to $UPGRADE_FROM first, following its guide)" ;;
  esac

  checkout=$(git -C "$REPO_ROOT" describe --tags --exact-match 2>/dev/null || true)
  if [[ "$checkout" != "$MAK4I_RELEASE" ]]; then
    warn "this checkout is ${checkout:-not at a release tag}, not $MAK4I_RELEASE. For a supported upgrade: git fetch --depth 1 origin tag $MAK4I_RELEASE && git checkout $MAK4I_RELEASE"
  fi
  service_running mak4i || die "$EX_FAILURE" "the current stack isn't running; start it (cd $COMPOSE_DIR && $(compose_hint) up -d) so it can be backed up first"
  before=$(db_counts)
  abefore=$(artifact_count)

  step "Backup (required before upgrading)"
  if [[ -n "$USE_BACKUP" ]]; then
    backup_dir=$(abs_path "$USE_BACKUP") || die "$EX_BACKUP" "no such backup: $USE_BACKUP"
    verify_backup "$backup_dir"
  else
    do_backup
    backup_dir=$BACKUP_DEST
  fi

  step "Images for $MAK4I_RELEASE (pinned versions)"
  if [[ "$PROFILE" == tls ]]; then run compose pull postgres caddy; else run compose pull postgres; fi || die "$EX_FAILURE" "could not pull the pinned images"
  run compose build --pull mak4i || die "$EX_FAILURE" "could not build the MAK4I image"

  step "Upgrade (containers are recreated; migrations run first)"
  wait_up "$TIMEOUT" || die "$EX_UNHEALTHY" "the upgraded stack did not become healthy within ${TIMEOUT}s. Roll back with the steps below.$(_rollback_text "$from" "$backup_dir")"

  step "Verify"
  verify_health || die "$EX_UNHEALTHY" "MAK4I is not healthy after the upgrade.$(_rollback_text "$from" "$backup_dir")"
  if [[ "$DRY_RUN" == 1 ]]; then say "Dry run complete: nothing was changed."; return; fi
  [[ "$(running_version)" == "$MAK4I_RELEASE" ]] || die "$EX_FAILURE" "the running version is $(running_version), expected $MAK4I_RELEASE"
  migration_at_head || die "$EX_FAILURE" "the database schema is not at the latest migration"
  after=$(db_counts)
  aafter=$(artifact_count)
  [[ "$before" == "$after" && "$abefore" == "$aafter" ]] ||
    die "$EX_FAILURE" "data counts changed during the upgrade (before: $before / $abefore, after: $after / $aafter).$(_rollback_text "$from" "$backup_dir")"
  info "running $MAK4I_RELEASE; data preserved: $after (organizations principals projects grants credentials), $aafter artifact file(s)"
  mism=$(collation_mismatch)
  if [[ -n "$mism" ]]; then
    warn "PostgreSQL reports a collation version change for: $mism. Rebuild indexes once (safe, brief):"
    warn "  cd $COMPOSE_DIR && docker compose exec postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"REINDEX DATABASE \\\"\$POSTGRES_DB\\\"\" -c \"ALTER DATABASE \\\"\$POSTGRES_DB\\\" REFRESH COLLATION VERSION\"'"
  fi
  if [[ "$PROFILE" == tls && -n "$DOMAIN" ]]; then
    local deadline=$((SECONDS + 120))
    until tls_ready "$DOMAIN" "$TLS_ISSUER"; do
      ((SECONDS < deadline)) || { warn "HTTPS not ready yet; check with: $0 status"; break; }
      sleep 3
    done
  fi
  env_set "$ENV_FILE" "MAK4I_BOOTSTRAP_PROFILE=$PROFILE" "MAK4I_INSTALL_STATE=complete" "MAK4I_INSTALLED_RELEASE=$MAK4I_RELEASE"
  say ""
  say "Upgraded to $MAK4I_RELEASE. Backup taken before the upgrade: $backup_dir"
  _rollback_text "$from" "$backup_dir"
}

_rollback_text() {
  cat <<EOF

Rollback to $1, if you need it:
  1. git -C $REPO_ROOT fetch --depth 1 origin tag $1 && git -C $REPO_ROOT checkout $1
  2. cd $COMPOSE_DIR && $(compose_hint) up -d --build --wait
  3. Only if the data must be reverted as well, restore the pre-upgrade
     backup ($2) with that release's documented restore procedure
     (control-plane.dump and artifacts/).
EOF
}

# --- uninstall ----------------------------------------------------------------

cmd_uninstall() {
  load_state
  local vols
  vols=$(existing_volumes)
  if [[ "$EXIST_STATE" == none && -z "$vols" ]]; then
    say "Nothing to uninstall: no configuration and no MAK4I data volumes found."
    return
  fi
  PROFILE=${STORED_PROFILE:-tls}
  if [[ "$DESTROY_DATA" == 1 ]]; then
    _uninstall_destroy "$vols"
  else
    _uninstall_preserve "$vols"
  fi
}

_uninstall_preserve() {
  local vols=$1
  step "Uninstall (preserving data)"
  run compose_all down --remove-orphans || die "$EX_FAILURE" "docker compose down failed"
  if [[ "$EXIST_STATE" != none && "$EXIST_STATE" != unmanaged ]]; then
    [[ "$DRY_RUN" == 1 ]] || env_set "$ENV_FILE" "MAK4I_INSTALL_STATE=preserved"
  fi
  cat <<EOF

Removed: the MAK4I, PostgreSQL, migration and Caddy containers and their network.
Kept:
  - data volumes: ${vols:-none} (database, artifacts, certificates)
  - configuration: $ENV_FILE (holds the database password; keep it)
  - backups: wherever you saved them (default $HOME/mak4i-backups)
Reinstall with the same data: $0 install
EOF
}

_uninstall_destroy() {
  local vols=$1 v
  cat <<EOF

PERMANENT DATA DELETION. This cannot be undone.
It deletes these Docker volumes:
EOF
  for v in $vols; do
    case "$v" in
      *_pgdata) info "$v  control-plane database: organizations, principals, projects, grants, credentials (every issued token stops working)" ;;
      *_artifacts) info "$v  every artifact, with its full lineage and history" ;;
      *_caddy_data) info "$v  TLS certificates and the ACME account" ;;
      *_caddy_config) info "$v  Caddy's runtime configuration" ;;
    esac
  done
  [[ -n "$vols" ]] || info "(no data volumes exist)"
  info "and the configuration file $ENV_FILE (database password)."
  info "Not deleted: backups, this repository, pinned third-party images."
  if [[ "$YES_DESTROY" != 1 ]]; then
    confirm_phrase "destroy mak4i data" ""
  fi

  step "Uninstall (destroying data)"
  run compose_all down --volumes --remove-orphans || die "$EX_FAILURE" "docker compose down failed"
  if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && "$ENV_FILE" == "$COMPOSE_DIR/.env" ]]; then
    run rm -f -- "$ENV_FILE"
  fi
  if [[ "$REMOVE_IMAGES" == 1 ]]; then
    run docker image rm mak4i-reference:local || warn "could not remove the mak4i-reference:local image"
  fi
  [[ "$DRY_RUN" == 1 ]] && { say "Dry run complete: nothing was changed."; return; }
  for v in $vols; do
    if docker volume inspect "$v" >/dev/null 2>&1; then
      die "$EX_FAILURE" "volume $v still exists after removal"
    fi
  done
  say ""
  say "Removed: ${vols:-no volumes}, the containers and network, and $ENV_FILE."
  say "MAK4I data on this host is gone. Backups (if any) were not touched."
}
