# shellcheck shell=bash
# Read-only discovery of this VM's public IPv4 address, used by preflight to
# check that the TLS domain points at this VM.
#
# Kept separate from the deployment logic on purpose: it only *reads* the
# cloud providers' link-local instance-metadata endpoints (short timeouts,
# no credentials) and never creates or changes any cloud resource, DNS
# record, firewall rule or IAM setting. On a VM where none answers (on
# premises, other clouds), pass --public-ip instead.

_md_curl() { curl -fsS -m 2 "$@" 2>/dev/null; }

# detect_public_ip — prints "IP PROVIDER" or nothing.
detect_public_ip() {
  local ip token
  command -v curl >/dev/null 2>&1 || return 0

  # Google Cloud: the external IP of the first access config.
  ip=$(_md_curl -H 'Metadata-Flavor: Google' \
    'http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip' || true)
  if _is_ipv4 "$ip"; then printf '%s gcp\n' "$ip"; return 0; fi

  # AWS (IMDSv2: session token first).
  token=$(_md_curl -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 'http://169.254.169.254/latest/api/token' || true)
  if [[ -n "$token" ]]; then
    ip=$(_md_curl -H "X-aws-ec2-metadata-token: $token" 'http://169.254.169.254/latest/meta-data/public-ipv4' || true)
    if _is_ipv4 "$ip"; then printf '%s aws\n' "$ip"; return 0; fi
  fi

  # Azure.
  ip=$(_md_curl -H 'Metadata: true' \
    'http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/publicIpAddress?api-version=2021-02-01&format=text' || true)
  if _is_ipv4 "$ip"; then printf '%s azure\n' "$ip"; return 0; fi
  return 0
}

_is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local octet
  for octet in "${BASH_REMATCH[@]:1}"; do
    ((10#$octet <= 255)) || return 1
  done
}
