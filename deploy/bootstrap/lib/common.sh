# shellcheck shell=bash
# Shared helpers for deploy/bootstrap/mak4i-enterprise: output, exit
# codes, dry-run, confirmations and path safety. Sourced, never executed.

# --- Exit codes (stable; documented in --help and the Enterprise guide) --
# shellcheck disable=SC2034 # used by the other bootstrap files
readonly EX_OK=0          # success
readonly EX_FAILURE=1     # a step failed (see the message and the log)
readonly EX_USAGE=2       # invalid command line
readonly EX_PREFLIGHT=3   # preflight found a FAIL
readonly EX_EXISTING=4    # refused: conflicting or unmanaged existing installation
readonly EX_UNHEALTHY=5   # deployment not healthy/ready (or certificate not issued) in time
readonly EX_ABORTED=6     # confirmation not given
readonly EX_UNSUPPORTED=7 # unsupported upgrade path or environment
readonly EX_BACKUP=8      # backup or restore failed validation

DRY_RUN=0
NON_INTERACTIVE=0
LOG_FILE=""

# --- Output ---------------------------------------------------------------

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  _C_RED=$'\033[31m' _C_YEL=$'\033[33m' _C_GRN=$'\033[32m' _C_BLD=$'\033[1m' _C_OFF=$'\033[0m'
else
  _C_RED="" _C_YEL="" _C_GRN="" _C_BLD="" _C_OFF=""
fi

say() { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$_C_BLD" "$*" "$_C_OFF"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%sWARNING:%s %s\n' "$_C_YEL" "$_C_OFF" "$*" >&2; }
err() { printf '%sERROR:%s %s\n' "$_C_RED" "$_C_OFF" "$*" >&2; }

# die CODE MESSAGE... — print an error (plus the log location) and exit.
die() {
  local code=$1
  shift
  err "$*"
  if [[ -n "$LOG_FILE" ]]; then
    printf '       Log: %s\n' "$LOG_FILE" >&2
  fi
  exit "$code"
}

# Preflight-style result lines. Counters are read by the preflight summary.
PF_PASS=0 PF_WARN=0 PF_FAIL=0
result() {
  local status=$1 name=$2 detail=${3:-}
  case "$status" in
    PASS) PF_PASS=$((PF_PASS + 1)); printf '  %s[PASS]%s %-34s %s\n' "$_C_GRN" "$_C_OFF" "$name" "$detail" ;;
    WARN) PF_WARN=$((PF_WARN + 1)); printf '  %s[WARN]%s %-34s %s\n' "$_C_YEL" "$_C_OFF" "$name" "$detail" ;;
    FAIL) PF_FAIL=$((PF_FAIL + 1)); printf '  %s[FAIL]%s %-34s %s\n' "$_C_RED" "$_C_OFF" "$name" "$detail" ;;
    INFO) printf '  [INFO] %-34s %s\n' "$name" "$detail" ;;
  esac
}

# --- Logging --------------------------------------------------------------

# Everything the command prints also goes to a timestamped log under the
# state directory (outside the repository and the Docker build context).
# Secrets are never printed, so the log never contains them either.
log_init() {
  local command=$1 state_dir
  state_dir=${MAK4I_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/mak4i-enterprise}
  (umask 077 && mkdir -p "$state_dir/logs") || return 0
  LOG_FILE="$state_dir/logs/$(date -u +%Y%m%dT%H%M%SZ)-$command.log"
  : >"$LOG_FILE" && chmod 600 "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
}

# --- Execution --------------------------------------------------------------

quote_cmd() {
  local out="" arg
  for arg in "$@"; do
    printf -v arg '%q' "$arg"
    out+="$arg "
  done
  printf '%s' "${out% }"
}

# run CMD... — execute a state-changing command, or only show it under
# --dry-run. Read-only commands are called directly, never through run.
run() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '    [dry-run] %s\n' "$(quote_cmd "$@")"
    return 0
  fi
  "$@"
}

require_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "$EX_FAILURE" "required command not found: $cmd"
  done
}

# confirm_phrase PHRASE PROMPT — require the operator to type PHRASE.
# Non-interactive runs must pass the command's explicit confirmation flag
# instead (checked by the caller before calling this).
confirm_phrase() {
  local phrase=$1 prompt=$2 answer
  if [[ "$NON_INTERACTIVE" == 1 || ! -t 0 ]]; then
    die "$EX_ABORTED" "confirmation required: re-run interactively, or pass the documented confirmation flag with --non-interactive"
  fi
  printf '%s\nType "%s" to continue: ' "$prompt" "$phrase"
  IFS= read -r answer || answer=""
  [[ "$answer" == "$phrase" ]] || die "$EX_ABORTED" "not confirmed; nothing was changed"
}

# --- Paths ------------------------------------------------------------------

# Absolute, symlink-free form of a path whose parent exists.
abs_path() {
  local path=$1 dir base
  if [[ -d "$path" ]]; then
    (cd -- "$path" && pwd -P)
    return
  fi
  dir=$(dirname -- "$path")
  base=$(basename -- "$path")
  [[ -d "$dir" ]] || return 1
  printf '%s/%s\n' "$(cd -- "$dir" && pwd -P)" "$base"
}

# is_within CHILD PARENT — CHILD is PARENT or below it (both absolute).
is_within() {
  local child=${1%/} parent=${2%/}
  [[ "$child" == "$parent" || "$child" == "$parent"/* ]]
}

# safe_backup_root PATH — refuse locations that are dangerous or that would
# land backups (database dump, .env copy) inside the repository, which is
# the Docker build context.
safe_backup_root() {
  local path=$1 existing rest="" resolved
  [[ -n "$path" ]] || die "$EX_USAGE" "backup directory is empty"
  [[ "$path" == /* ]] || die "$EX_USAGE" "backup directory must be an absolute path: $path"
  case "/$path/" in
    */../* | */./*) die "$EX_USAGE" "backup directory must not contain . or .. components: $path" ;;
  esac
  # Validate before creating anything: resolve the nearest existing
  # ancestor (following symlinks) and re-attach the not-yet-existing part.
  existing=${path%/}
  while [[ -n "$existing" && ! -d "$existing" ]]; do
    rest="/$(basename -- "$existing")$rest"
    existing=$(dirname -- "$existing")
  done
  [[ -n "$existing" ]] || existing=/
  resolved="$(cd -- "$existing" && pwd -P)$rest"
  resolved=${resolved#/}
  resolved="/${resolved%/}"
  case "$resolved" in
    / | /bin | /boot | /dev | /etc | /lib | /lib64 | /proc | /root | /sbin | /sys | /usr | /var | /tmp | /home | "$HOME")
      die "$EX_USAGE" "refusing to use $resolved itself for backups; use a dedicated directory such as $HOME/mak4i-backups" ;;
  esac
  if is_within "$resolved" "$REPO_ROOT"; then
    die "$EX_USAGE" "refusing to write backups inside the repository ($REPO_ROOT), which is the Docker build context"
  fi
  (umask 077 && mkdir -p -- "$resolved") 2>/dev/null || die "$EX_BACKUP" "cannot create backup directory: $resolved"
  printf '%s\n' "$resolved"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    shasum -a 256 -- "$1" | awk '{print $1}'
  fi
}

utc_stamp() { date -u +%Y%m%dT%H%M%SZ; }
