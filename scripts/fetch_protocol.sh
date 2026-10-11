#!/usr/bin/env bash
# Check out the pinned MAK4I protocol revision (PROTOCOL_REVISION) into
# .protocol/ for the conformance tests. Idempotent; refuses to leave a
# checkout at any other revision.
set -euo pipefail
cd "$(dirname "$0")/.."
read -r repo sha < <(grep -v '^#' PROTOCOL_REVISION | grep -v '^[[:space:]]*$' | head -1)
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "PROTOCOL_REVISION must pin a full commit SHA" >&2; exit 2; }
dest=.protocol
if [[ -d "$dest/.git" ]] && [[ "$(git -C "$dest" rev-parse HEAD)" == "$sha" ]]; then
  echo "protocol already at $sha"
  exit 0
fi
rm -rf "$dest"
git init -q "$dest"
git -C "$dest" remote add origin "$repo"
git -C "$dest" fetch -q --depth 1 origin "$sha"
git -C "$dest" checkout -q --detach FETCH_HEAD
[[ "$(git -C "$dest" rev-parse HEAD)" == "$sha" ]] || { echo "fetched revision mismatch" >&2; exit 1; }
echo "protocol at $sha"
