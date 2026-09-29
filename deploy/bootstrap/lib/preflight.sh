# shellcheck shell=bash
# Preflight: read-only checks that the host, network and configuration are
# ready for an Enterprise Self-Hosted installation. Every check reports
# PASS, WARN or FAIL; any FAIL blocks `install`. Nothing here changes the
# host — except the opt-in --check-inbound probe, which briefly runs a
# throwaway container on port 80 and says so.

readonly SUPPORTED_OS="ubuntu:22.04 ubuntu:24.04 debian:12 debian:13"
readonly MIN_CPUS=2
readonly MEM_OK_KB=3670016    # ~3.5 GiB  (the verified GCP VM had 8 GB)
readonly MEM_MIN_KB=1887436   # ~1.8 GiB
readonly DISK_OK_KB=20971520  # 20 GiB free
readonly DISK_MIN_KB=10485760 # 10 GiB free

OS_RELEASE_FILE=${MAK4I_BOOTSTRAP_OS_RELEASE:-/etc/os-release}
MEMINFO_FILE=${MAK4I_BOOTSTRAP_MEMINFO:-/proc/meminfo}

is_loopback_ip() { [[ "$1" == 127.* || "$1" == ::1 || "$1" == localhost ]]; }

# RFC 1918, carrier-grade NAT and link-local ranges.
is_private_ip() {
  [[ "$1" =~ ^10\. || "$1" =~ ^192\.168\. || "$1" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ||
    "$1" =~ ^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\. || "$1" =~ ^169\.254\. ]]
}

valid_domain() {
  local d=$1
  ((${#d} <= 253)) || return 1
  [[ "$d" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || return 1
  ! _is_ipv4 "$d"
}

# listening_ports — TCP ports with a listener on this host.
listening_ports() {
  if command -v ss >/dev/null 2>&1; then
    ss -Htln 2>/dev/null | awk '{ n = split($4, a, ":"); print a[n] }' | sort -un || true
    return
  fi
  local file addr state
  for file in /proc/net/tcp /proc/net/tcp6; do
    [[ -r "$file" ]] || continue
    while read -r _ addr _ state _; do
      [[ "$state" == 0A ]] || continue
      printf '%d\n' "$((16#${addr##*:}))"
    done < <(tail -n +2 "$file")
  done | sort -un
}

port_listening() { listening_ports | grep -qx "$1"; }

# resolve_ipv4 NAME — A records through this host's resolver (which is what
# `curl` here uses, including any negatively cached answer).
resolve_ipv4() {
  getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u || true
}

# resolve_public NAME — A records from a public resolver, bypassing the
# local cache. Empty if `dig` isn't installed.
resolve_public() {
  command -v dig >/dev/null 2>&1 || return 0
  dig +short +time=3 +tries=1 A "$1" @1.1.1.1 2>/dev/null | grep -E '^[0-9.]+$' | sort -u || true
}

https_reachable() {
  local code
  code=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$1" 2>/dev/null) || code=000
  [[ "$code" != 000 ]]
}

_pf_os() {
  local kernel id version name
  kernel=$(uname -s)
  if [[ "$kernel" != Linux ]]; then
    result FAIL "Operating system" "$kernel is not supported; use a Linux VM"
    return
  fi
  id=$(env_get "$OS_RELEASE_FILE" ID)
  version=$(env_get "$OS_RELEASE_FILE" VERSION_ID)
  name=$(env_get "$OS_RELEASE_FILE" PRETTY_NAME)
  if [[ " $SUPPORTED_OS " == *" $id:$version "* ]]; then
    result PASS "Linux distribution" "${name:-$id $version}"
  else
    result WARN "Linux distribution" "${name:-unknown} is not a tested distribution (tested: Ubuntu 22.04/24.04, Debian 12/13)"
  fi
}

_pf_arch() {
  case "$(uname -m)" in
    x86_64 | amd64) result PASS "Architecture" "x86-64" ;;
    aarch64 | arm64) result WARN "Architecture" "arm64 is not tested in this release (x86-64 is)" ;;
    *) result FAIL "Architecture" "$(uname -m) is not supported (x86-64 required)" ;;
  esac
}

_pf_resources() {
  local cpus mem_kb
  cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)
  if ((cpus >= MIN_CPUS)); then
    result PASS "CPU" "$cpus vCPU"
  else
    result WARN "CPU" "$cpus vCPU (2 or more recommended)"
  fi
  mem_kb=$(awk '/^MemTotal:/ {print $2}' "$MEMINFO_FILE" 2>/dev/null || true)
  if [[ -z "$mem_kb" ]]; then
    result WARN "Memory" "could not read $MEMINFO_FILE"
  elif ((mem_kb >= MEM_OK_KB)); then
    result PASS "Memory" "$((mem_kb / 1024)) MiB"
  elif ((mem_kb >= MEM_MIN_KB)); then
    result WARN "Memory" "$((mem_kb / 1024)) MiB (4 GiB or more recommended)"
  else
    result FAIL "Memory" "$((mem_kb / 1024)) MiB (at least 2 GiB required)"
  fi
}

DOCKER_OK=0
_pf_docker() {
  local out compose_version major root free_kb
  if ! command -v docker >/dev/null 2>&1; then
    result FAIL "Docker Engine" "not installed; install Docker Engine for your distribution (https://docs.docker.com/engine/install/)"
    result FAIL "Docker Compose plugin" "not checked (no Docker)"
    return
  fi
  if ! out=$(docker info --format '{{.ServerVersion}}' 2>&1); then
    if [[ "$out" == *"permission denied"* ]]; then
      result FAIL "Docker access" "this user can't use Docker: sudo usermod -aG docker \"\$USER\", then log out and back in"
    else
      result FAIL "Docker Engine" "installed but not running: sudo systemctl enable --now docker"
    fi
    return
  fi
  result PASS "Docker Engine" "running (server $out), usable by $(id -un)"
  DOCKER_OK=1
  if compose_version=$(docker compose version --short 2>/dev/null); then
    major=${compose_version#v}
    major=${major%%.*}
    if ((major >= 2)); then
      result PASS "Docker Compose plugin" "v${compose_version#v}"
    else
      result FAIL "Docker Compose plugin" "v${compose_version#v} is too old (v2 or later required)"
    fi
  else
    result FAIL "Docker Compose plugin" "missing: install the docker-compose-plugin package"
  fi
  root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  [[ -d "$root" ]] || root=/
  free_kb=$(df -Pk "$root" 2>/dev/null | awk 'NR == 2 {print $4}' || true)
  if [[ -z "$free_kb" ]]; then
    result WARN "Free disk" "could not determine free space for $root"
  elif ((free_kb >= DISK_OK_KB)); then
    result PASS "Free disk" "$((free_kb / 1048576)) GiB free for Docker ($root)"
  elif ((free_kb >= DISK_MIN_KB)); then
    result WARN "Free disk" "$((free_kb / 1048576)) GiB free for Docker ($root); 20 GiB recommended"
  else
    result FAIL "Free disk" "$((free_kb / 1048576)) GiB free for Docker ($root); at least 10 GiB required"
  fi
}

_pf_repo() {
  local f missing=""
  for f in deploy/compose/compose.yaml deploy/compose/Caddyfile deploy/compose/.env.example Dockerfile; do
    [[ -f "$REPO_ROOT/$f" ]] || missing+="$f "
  done
  if [[ -z "$missing" ]]; then
    result PASS "Repository files" "$REPO_ROOT"
  else
    result FAIL "Repository files" "missing: $missing(run from a complete $MAK4I_RELEASE checkout)"
  fi
}

_pf_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    result PASS "Configuration (.env)" "not created yet; install generates it (mode 600, random database password)"
    return
  fi
  if [[ -L "$ENV_FILE" ]]; then
    result FAIL "Configuration (.env)" "$ENV_FILE is a symlink; replace it with a regular file"
    return
  fi
  if ! env_mode_ok "$ENV_FILE"; then
    result WARN "Configuration (.env)" "readable by other users: chmod 600 $ENV_FILE"
  fi
  if [[ -z "$(env_get "$ENV_FILE" POSTGRES_PASSWORD)" ]]; then
    result FAIL "Configuration (.env)" "POSTGRES_PASSWORD is empty"
  else
    result PASS "Configuration (.env)" "present; required values set"
  fi
}

_pf_domain_and_dns() {
  local expected="" provider="" local_ips public_ips
  if [[ -z "$DOMAIN" ]]; then
    result FAIL "Domain" "--domain is required for the tls profile"
    return
  fi
  if ! valid_domain "$DOMAIN"; then
    result FAIL "Domain" "'$DOMAIN' is not a valid lowercase DNS name"
    return
  fi
  result PASS "Domain" "$DOMAIN"

  if [[ -n "$PUBLIC_IP" ]]; then
    expected=$PUBLIC_IP provider="--public-ip"
  else
    read -r expected provider < <(detect_public_ip) || true
  fi
  local_ips=$(resolve_ipv4 "$DOMAIN" | tr '\n' ' ')
  public_ips=$(resolve_public "$DOMAIN" | tr '\n' ' ')
  local severity=FAIL
  [[ "$TLS_ISSUER" == internal ]] && severity=WARN

  if [[ -z "$local_ips" && -z "$public_ips" ]]; then
    result "$severity" "DNS A record" "$DOMAIN does not resolve (NXDOMAIN). Create the A record; certificate requests fail with NXDOMAIN until it has propagated"
  elif [[ -z "$local_ips" ]]; then
    result WARN "DNS A record" "public DNS answers ${public_ips% } but this VM's resolver doesn't yet (cached negative answer). It expires on its own; meanwhile test with curl --resolve $DOMAIN:443:<ip>"
  elif [[ -z "$expected" ]]; then
    result WARN "DNS A record" "$DOMAIN -> ${local_ips% }; couldn't determine this VM's public IP to compare (pass --public-ip)"
  elif [[ " $local_ips" == *" $expected "* ]]; then
    result PASS "DNS A record" "$DOMAIN -> $expected (this VM, from $provider)"
  else
    result "$severity" "DNS A record" "$DOMAIN -> ${local_ips% }, but this VM's public IP is $expected ($provider). Point the A record at $expected (a static IP is recommended)"
  fi
}

_pf_ports() {
  local port ours=0
  if [[ "$PROFILE" == tls ]]; then
    [[ "$DOCKER_OK" == 1 ]] && service_running caddy && ours=1
    for port in 80 443; do
      if ! port_listening "$port"; then
        result PASS "Local port $port" "free"
      elif [[ "$ours" == 1 ]]; then
        result PASS "Local port $port" "in use by this MAK4I deployment (Caddy)"
      else
        result FAIL "Local port $port" "already in use by another process; free it for Caddy"
      fi
    done
  fi
  if port_listening "$HTTP_PORT" && ! { [[ "$DOCKER_OK" == 1 ]] && service_running mak4i; }; then
    result FAIL "Local port $HTTP_PORT" "already in use by another process (MAK4I's HTTP port); choose another with --http-port"
  fi
}

_pf_bind() {
  if [[ "$PROFILE" == tls ]]; then
    local stored
    stored=$(env_get "$ENV_FILE" MAK4I_HTTP_BIND)
    if [[ -n "$stored" && "$stored" != 127.0.0.1 ]]; then
      result FAIL "Port $HTTP_PORT exposure" "MAK4I_HTTP_BIND=$stored in .env; with the tls profile it must be 127.0.0.1 (only Caddy is public)"
    else
      result PASS "Port $HTTP_PORT exposure" "127.0.0.1 only; PostgreSQL is never published"
    fi
    return
  fi
  if is_loopback_ip "$BIND"; then
    result PASS "Port $HTTP_PORT exposure" "$BIND only (reach it through an SSH tunnel or a local proxy); PostgreSQL is never published"
  elif is_private_ip "$BIND"; then
    result WARN "Port $HTTP_PORT exposure" "plain HTTP on private address $BIND: bearer tokens cross that network unencrypted. Use only on a trusted network or behind your TLS load balancer"
  elif [[ "$ALLOW_PUBLIC_BIND" == 1 ]]; then
    result WARN "Port $HTTP_PORT exposure" "plain HTTP on $BIND (--allow-public-bind). Restrict it with a firewall to your load balancer; never expose it to the internet"
  else
    result FAIL "Port $HTTP_PORT exposure" "$BIND would expose plain HTTP beyond a private network. Use a private address, the tls profile, or --allow-public-bind behind a firewall"
  fi
}

_pf_outbound() {
  if ! command -v curl >/dev/null 2>&1; then
    result FAIL "Outbound HTTPS" "curl is not installed (sudo apt-get install curl)"
    return
  fi
  if https_reachable https://registry-1.docker.io/v2/; then
    result PASS "Outbound HTTPS: Docker Hub" "reachable (images)"
  elif [[ "$DOCKER_OK" == 1 ]] && docker image inspect "$(pinned_image postgres)" >/dev/null 2>&1; then
    result WARN "Outbound HTTPS: Docker Hub" "unreachable, but the pinned images are already present"
  else
    result FAIL "Outbound HTTPS: Docker Hub" "unreachable; images can't be pulled"
  fi
  if https_reachable https://ghcr.io/v2/; then
    result PASS "Outbound HTTPS: ghcr.io" "reachable (build tooling)"
  else
    result FAIL "Outbound HTTPS: ghcr.io" "unreachable; the MAK4I image can't be built"
  fi
  if [[ "$PROFILE" == tls && "$TLS_ISSUER" == acme ]]; then
    if https_reachable https://acme-v02.api.letsencrypt.org/directory; then
      result PASS "Outbound HTTPS: Let's Encrypt" "reachable (certificates)"
    else
      result FAIL "Outbound HTTPS: Let's Encrypt" "unreachable; certificates can't be issued"
    fi
  fi
}

_pf_inbound() {
  [[ "$PROFILE" == tls && "$TLS_ISSUER" == acme ]] || return 0
  if [[ "$CHECK_INBOUND" != 1 ]]; then
    result INFO "Inbound 80/443 from the internet" "not tested (add --check-inbound). The cloud firewall must allow TCP 80 and 443; on GCP they are blocked until allowed"
    return
  fi
  if [[ "$DOCKER_OK" != 1 ]] || port_listening 80; then
    result WARN "Inbound port 80" "not tested (Docker unavailable or port 80 in use)"
    return
  fi
  local token body name=mak4i-preflight-probe
  token=$(new_secret | cut -c1-24)
  info "--check-inbound: running a temporary responder container on port 80 for a few seconds"
  if ! docker run -d --rm --name "$name" -p 80:80 "$(pinned_image caddy)" \
    caddy respond --listen :80 --body "$token" >/dev/null 2>&1; then
    result WARN "Inbound port 80" "could not start the temporary responder"
    return
  fi
  sleep 2
  body=$(curl -sS -m 8 "http://$DOMAIN/" 2>/dev/null) || body=""
  docker rm -f "$name" >/dev/null 2>&1 || true
  if [[ "$body" == "$token" ]]; then
    result PASS "Inbound port 80" "http://$DOMAIN/ reaches this VM"
  else
    result WARN "Inbound port 80" "couldn't confirm (firewall, DNS, or the VM can't reach its own public IP). From another machine: curl -v http://$DOMAIN/"
  fi
}

_pf_existing() {
  local state vols=""
  if [[ -f "$ENV_FILE" ]]; then
    state=$(env_get "$ENV_FILE" MAK4I_INSTALL_STATE)
    result INFO "Existing configuration" "$ENV_FILE (${state:-manual installation, not managed by this bootstrap})"
  fi
  if [[ "$DOCKER_OK" == 1 ]]; then
    vols=$(existing_volumes)
    [[ -z "$vols" ]] || result INFO "Existing data volumes" "$vols"
    if service_running mak4i; then
      result INFO "Running deployment" "MAK4I $(running_version) is running"
    fi
    if [[ -n "$vols" && ! -f "$ENV_FILE" ]]; then
      result WARN "Existing data" "volumes exist without $ENV_FILE; install will refuse (restore .env from a backup, or uninstall --destroy-data)"
    fi
  fi
}

_pf_compose_config() {
  [[ "$DOCKER_OK" == 1 ]] || return 0
  local out
  if [[ -f "$ENV_FILE" ]]; then
    out=$(compose config -q 2>&1 || true)
  else
    out=$(POSTGRES_PASSWORD=preflight-placeholder MAK4I_DOMAIN=${DOMAIN:-example.invalid} \
      docker compose --project-directory "$COMPOSE_DIR" --file "$COMPOSE_DIR/compose.yaml" \
      --env-file "$COMPOSE_DIR/.env.example" --profile tls config -q 2>&1 || true)
  fi
  if [[ -z "$out" ]]; then
    result PASS "Compose configuration" "valid"
  else
    result FAIL "Compose configuration" "$(printf '%s' "$out" | head -n 1)"
  fi
}

# preflight — run every check; returns 0 unless a check FAILed.
preflight() {
  PF_PASS=0 PF_WARN=0 PF_FAIL=0
  say "Preflight for MAK4I Enterprise $MAK4I_RELEASE (profile: $PROFILE${DOMAIN:+, domain: $DOMAIN})"
  _pf_os
  _pf_arch
  _pf_resources
  _pf_docker
  _pf_repo
  _pf_env
  [[ "$PROFILE" == tls ]] && _pf_domain_and_dns
  _pf_ports
  _pf_bind
  _pf_outbound
  _pf_inbound
  _pf_existing
  _pf_compose_config
  say ""
  say "Preflight: $PF_PASS passed, $PF_WARN warning(s), $PF_FAIL failed."
  ((PF_FAIL == 0))
}
