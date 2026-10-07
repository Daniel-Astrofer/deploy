#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
SCRIPT="$ROOT/infra/runtime/tor/seed-onion-identity.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

source_dir="$TMP/source"
state_root="$TMP/tor-state"
target_dir="$state_root/kerosene-test-identity"
mkdir -p "$source_dir"
trap 'rm -rf "$TMP" "$target_dir"' EXIT HUP INT TERM

python3 - "$source_dir" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
(root / "hs_ed25519_secret_key").write_bytes(b"== ed25519v1-secret: type0 ==\0\0\0" + b"s" * 64)
(root / "hs_ed25519_public_key").write_bytes(b"== ed25519v1-public: type0 ==\0\0\0" + b"p" * 32)
(root / "hostname").write_text("a" * 56 + ".onion\n")
PY

KEROSENE_TOR_IDENTITY_SOURCE="$source_dir" \
KEROSENE_TOR_HIDDEN_SERVICE_DIR="$target_dir" \
KEROSENE_TOR_STATE_ROOT="$state_root" \
KEROSENE_TOR_IDENTITY_OWNER="$(id -u):$(id -g)" sh "$SCRIPT"

cmp "$source_dir/hs_ed25519_secret_key" "$target_dir/hs_ed25519_secret_key"
cmp "$source_dir/hs_ed25519_public_key" "$target_dir/hs_ed25519_public_key"
[ ! -e "$target_dir/hostname" ]
[ "$(stat -c %a "$target_dir")" = 700 ]
[ "$(stat -c %a "$target_dir/hs_ed25519_secret_key")" = 600 ]

# Reapplying identical authority is idempotent; drift is rejected without overwrite.
cp "$source_dir/hostname" "$target_dir/hostname"
KEROSENE_TOR_IDENTITY_SOURCE="$source_dir" \
KEROSENE_TOR_HIDDEN_SERVICE_DIR="$target_dir" \
KEROSENE_TOR_STATE_ROOT="$state_root" \
KEROSENE_TOR_IDENTITY_OWNER="$(id -u):$(id -g)" sh "$SCRIPT"
printf 'b%.0s' $(seq 1 56) > "$source_dir/hostname"
printf '.onion\n' >> "$source_dir/hostname"
if KEROSENE_TOR_IDENTITY_SOURCE="$source_dir" \
   KEROSENE_TOR_HIDDEN_SERVICE_DIR="$target_dir" \
   KEROSENE_TOR_STATE_ROOT="$state_root" \
   KEROSENE_TOR_IDENTITY_OWNER="$(id -u):$(id -g)" sh "$SCRIPT" 2>/dev/null; then
  echo "changed identity was accepted" >&2
  exit 1
fi
grep -Eq '^a{56}\.onion$' "$target_dir/hostname"

# Invalid paths and malformed identity material fail closed.
if KEROSENE_TOR_IDENTITY_SOURCE="$source_dir" \
   KEROSENE_TOR_HIDDEN_SERVICE_DIR="$TMP/outside" \
   KEROSENE_TOR_STATE_ROOT="$state_root" sh "$SCRIPT" 2>/dev/null; then
  echo "unsafe target path was accepted" >&2
  exit 1
fi
truncate -s 1 "$source_dir/hs_ed25519_secret_key"
if KEROSENE_TOR_IDENTITY_SOURCE="$source_dir" \
   KEROSENE_TOR_HIDDEN_SERVICE_DIR="/var/lib/tor/kerosene-test-invalid-$$" \
   KEROSENE_TOR_STATE_ROOT="/var/lib/tor" \
   KEROSENE_TOR_IDENTITY_OWNER="$(id -u):$(id -g)" sh "$SCRIPT" 2>/dev/null; then
  echo "malformed identity was accepted" >&2
  exit 1
fi
