# shellcheck shell=bash
# Reading and writing the Compose .env file.
#
# The .env file is DATA, never shell code: it is parsed line by line with
# awk and never `source`d, so a value like `$(...)` is just text. Writes go
# to a mode-600 temporary file in the same directory and are renamed into
# place, so the file is never world-readable, even briefly. Secret values
# are passed to awk through the environment, never on a command line
# (which other local users could read from the process list).

readonly _ENV_KEY_RE='^[A-Za-z_][A-Za-z0-9_]*$'

# env_get FILE KEY — the value of KEY (last occurrence wins), with one
# layer of matching surrounding quotes removed. Prints nothing if unset.
env_get() {
  local file=$1 key=$2
  [[ -f "$file" ]] || return 0
  [[ "$key" =~ $_ENV_KEY_RE ]] || return 1
  awk -v key="$key" '
    /^[[:space:]]*#/ || !/=/ { next }
    {
      k = substr($0, 1, index($0, "=") - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
      if (k != key) next
      v = substr($0, index($0, "=") + 1)
      if (v ~ /^".*"$/ || v ~ /^'"'"'.*'"'"'$/) v = substr(v, 2, length(v) - 2)
      val = v; found = 1
    }
    END { if (found) print val }
  ' "$file"
}

# env_value_ok VALUE — safe to store unquoted in a Compose env file:
# no newline, quote, `$` (Compose interpolation), `#` (comment) or
# backslash.
env_value_ok() {
  case "$1" in
    *$'\n'* | *\"* | *\'* | *\$* | *\#* | *\\*) return 1 ;;
  esac
  return 0
}

# env_write FILE TEMPLATE KEY=VALUE... — write FILE from TEMPLATE (or an
# empty file), replacing each KEY's line or appending it. VALUEs come in as
# arguments here only for non-secret keys; POSTGRES_PASSWORD is read from
# the MAK4I_BOOTSTRAP_SECRET environment variable when its value is the
# literal "@secret".
env_write() {
  local file=$1 template=$2
  shift 2
  local dir tmp pair key
  dir=$(dirname -- "$file")
  for pair in "$@"; do
    key=${pair%%=*}
    [[ "$key" =~ $_ENV_KEY_RE ]] || die "$EX_FAILURE" "invalid .env key: $key"
    env_value_ok "${pair#*=}" || die "$EX_USAGE" "value for $key contains a character .env can't hold safely (newline, quote, \$, # or \\)"
  done
  tmp=$(umask 077 && mktemp "$dir/.env.tmp.XXXXXX") || die "$EX_FAILURE" "cannot create a temporary file in $dir"
  chmod 600 "$tmp"
  if ! awk '
    BEGIN {
      for (i = 1; i < ARGC; i++) {
        p = ARGV[i]; k = substr(p, 1, index(p, "=") - 1); v = substr(p, index(p, "=") + 1)
        if (v == "@secret") v = ENVIRON["MAK4I_BOOTSTRAP_SECRET"]
        want[k] = v; order[++n] = k
      }
      ARGC = 1
    }
    {
      line = $0
      if (line !~ /^[[:space:]]*#/ && index(line, "=") > 0) {
        k = substr(line, 1, index(line, "=") - 1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
        if (k in want) { if (!(k in done)) { print k "=" want[k]; done[k] = 1 }; next }
      }
      print line
    }
    END { for (i = 1; i <= n; i++) if (!(order[i] in done)) print order[i] "=" want[order[i]] }
  ' "$@" <"${template:-/dev/null}" >"$tmp"; then
    rm -f -- "$tmp"
    die "$EX_FAILURE" "could not write $file"
  fi
  mv -f -- "$tmp" "$file"
  chmod 600 "$file"
}

# env_set FILE KEY=VALUE... — update keys in an existing FILE in place
# (atomically, mode 600), keeping every other line.
env_set() {
  local file=$1
  shift
  local copy
  copy=$(umask 077 && mktemp "$(dirname -- "$file")/.env.src.XXXXXX") || die "$EX_FAILURE" "cannot create a temporary file"
  cp -- "$file" "$copy"
  env_write "$file" "$copy" "$@"
  rm -f -- "$copy"
}

# env_mode_ok FILE — not readable or writable by group/others.
env_mode_ok() {
  local perms
  # shellcheck disable=SC2012 # portable mode check (GNU and BSD stat differ)
  perms=$(ls -ln -- "$1" 2>/dev/null | awk '{print $1}' || true)
  [[ "$perms" == -rw-------* || "$perms" == -r--------* ]]
}

# new_secret — 64 hex characters from the kernel CSPRNG. URL-safe, so it can
# be embedded in the database URL unchanged.
new_secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}
