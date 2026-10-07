#!/bin/sh
# Install an externally authorized Tor v3 service identity exactly once.
set -eu

source_dir=${KEROSENE_TOR_IDENTITY_SOURCE:?KEROSENE_TOR_IDENTITY_SOURCE is required}
target_dir=${KEROSENE_TOR_HIDDEN_SERVICE_DIR:?KEROSENE_TOR_HIDDEN_SERVICE_DIR is required}
owner=${KEROSENE_TOR_IDENTITY_OWNER:-65532:65532}
state_root=${KEROSENE_TOR_STATE_ROOT:-/var/lib/tor}

case "$state_root" in
  /*/../*|*/..|*//*|*\ *) echo "invalid Tor state root" >&2; exit 1 ;;
  /*) ;;
  *) echo "Tor state root must be absolute" >&2; exit 1 ;;
esac
case "$target_dir" in
  "$state_root"/*) ;;
  *) echo "Tor hidden-service directory must be below /var/lib/tor" >&2; exit 1 ;;
esac

require_size() {
  file=$1
  expected=$2
  actual=$(wc -c < "$staging_dir/$file" | tr -d ' ')
  [ "$actual" = "$expected" ] || { echo "invalid Tor identity input size: $file" >&2; exit 1; }
}

install -d -m 0700 "$target_dir"
staging_dir="$target_dir/.identity-seed.$$"
cleanup() { rm -rf "$staging_dir"; }
trap cleanup EXIT HUP INT TERM
install -d -m 0700 "$staging_dir"
for file in hs_ed25519_secret_key hs_ed25519_public_key hostname; do
  [ -f "$source_dir/$file" ] || { echo "missing Tor identity input: $file" >&2; exit 1; }
  install -m 0600 "$source_dir/$file" "$staging_dir/$file"
done

require_size hs_ed25519_secret_key 96
require_size hs_ed25519_public_key 64
require_size hostname 63
grep -Eq '^[a-z2-7]{56}\.onion$' "$staging_dir/hostname" || {
  echo "invalid Tor v3 hostname" >&2
  exit 1
}

for file in hs_ed25519_secret_key hs_ed25519_public_key hostname; do
  if [ -e "$target_dir/$file" ]; then
    cmp -s "$staging_dir/$file" "$target_dir/$file" || {
      echo "existing Tor identity differs from authorized input: $file" >&2
      exit 1
    }
  fi
done
for file in hs_ed25519_secret_key hs_ed25519_public_key; do
  [ -e "$target_dir/$file" ] && continue
  mode=0600
  [ "$file" = hostname ] && mode=0644
  chmod "$mode" "$staging_dir/$file"
  chown "$owner" "$staging_dir/$file"
  mv "$staging_dir/$file" "$target_dir/$file"
done

chown "$owner" "$target_dir"
chmod 0700 "$target_dir"
