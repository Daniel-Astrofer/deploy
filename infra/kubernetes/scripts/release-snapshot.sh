#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RELEASE_DIR="${RELEASE_DIR:-$REPO_ROOT/release}"
MANIFEST="$RELEASE_DIR/release-manifest.json"
SIGNATURE="$MANIFEST.sig"
PRIVATE_KEY="$RELEASE_DIR/release-private-key.pem"
PUBLIC_KEY_B64="$RELEASE_DIR/release-public-key.der.b64"

usage() {
  cat <<'EOF'
Usage: scripts/release-snapshot.sh generate|validate

generate  Build a signed release manifest with git SHA, source hash, allowed config hash, SBOM path when available, and service image metadata.
validate  Verify manifest signature and recompute source/config hashes against the current checkout.
EOF
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

hash_paths() {
  local -a paths=("$@")
  (
    cd "$REPO_ROOT"
    for path in "${paths[@]}"; do
      if [[ -e "$path" ]]; then
        find "$path" \
          \( -path '*/build/*' \
             -o -path '*/.gradle/*' \
             -o -path '*/.dart_tool/*' \
             -o -path '*/target/*' \
             -o -path '*/*.root-owned-*' \
             -o -path '*/linux/flutter/ephemeral*' \
             -o -path '*/windows/flutter/ephemeral*' \
             -o -path '*/macos/Flutter/ephemeral*' \
             -o -path '*/tor/keys/*' \
             -o -path '*/certs/*' \) -prune \
          -o -type f -readable -print 2>/dev/null
      fi
    done | LC_ALL=C sort | while IFS= read -r file; do
      sha256sum "$file"
    done | sha256sum | awk '{print $1}'
  )
}

existing_paths() {
  local path
  for path in "$@"; do
    [[ -e "$REPO_ROOT/$path" ]] && printf '%s\n' "$path"
  done
}

release_source_paths() {
  existing_paths backend frontend scripts infra docker-compose.yml
}

release_config_paths() {
  existing_paths backend/kerosene/src/main/resources infra docker-compose.yml
}

ensure_keys() {
  mkdir -p "$RELEASE_DIR"
  chmod 700 "$RELEASE_DIR"
  if [[ ! -f "$PRIVATE_KEY" ]]; then
    openssl genpkey -algorithm Ed25519 -out "$PRIVATE_KEY" >/dev/null
    chmod 600 "$PRIVATE_KEY"
  fi
  openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER 2>/dev/null | base64 | tr -d '\n' > "$PUBLIC_KEY_B64"
  printf '\n' >> "$PUBLIC_KEY_B64"
}

image_digest() {
  local image="$1"
  if command -v docker >/dev/null 2>&1; then
    docker image inspect "$image" --format '{{index .RepoDigests 0}}' 2>/dev/null | sed 's/.*@//' || true
  fi
}

write_manifest() {
  local git_commit source_hash config_hash build_time sbom_path
  git_commit="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || printf 'unknown')"
  source_hash="$(hash_paths $(release_source_paths))"
  config_hash="$(hash_paths $(release_config_paths))"
  build_time="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  sbom_path=""

  if command -v syft >/dev/null 2>&1; then
    sbom_path="$RELEASE_DIR/sbom.spdx.json"
    syft dir:"$REPO_ROOT" -o spdx-json="$sbom_path" >/dev/null
  fi

  python3 - "$MANIFEST" "$git_commit" "$source_hash" "$config_hash" "$build_time" "$sbom_path" <<'PY'
import json
import os
import sys

manifest_path, git_commit, source_hash, config_hash, build_time, sbom_path = sys.argv[1:]
services = {
    "kerosene-backend": {
        "image": os.environ.get("KEROSENE_BACKEND_IMAGE", "kerosene-app"),
        "imageDigest": os.environ.get("IMAGE_DIGEST", "unknown"),
        "gitCommit": git_commit,
        "buildTime": build_time,
        "codeHash": "sha256:" + source_hash,
        "configHash": "sha256:" + config_hash,
    },
    "mpc-sidecar": {
        "image": os.environ.get("MPC_SIDECAR_IMAGE", "mpc-sidecar"),
        "imageDigest": os.environ.get("MPC_IMAGE_DIGEST", "unknown"),
        "gitCommit": git_commit,
        "buildTime": build_time,
        "codeHash": "sha256:" + source_hash,
        "configHash": "sha256:" + config_hash,
    },
    "web-admin": {
        "image": os.environ.get("WEB_ADMIN_IMAGE", "nginx:1.27-alpine"),
        "imageDigest": os.environ.get("WEB_ADMIN_IMAGE_DIGEST", "unknown"),
        "gitCommit": git_commit,
        "buildTime": build_time,
        "codeHash": "sha256:" + source_hash,
        "configHash": "sha256:" + config_hash,
    },
}
manifest = {
    "schema": "kerosene.release/v1",
    "version": os.environ.get("RELEASE_VERSION", "pre-alpha"),
    "createdAt": build_time,
    "gitCommit": git_commit,
    "sourceTreeHash": "sha256:" + source_hash,
    "allowedConfigHash": "sha256:" + config_hash,
    "sbom": sbom_path,
    "services": services,
}
with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

sign_manifest() {
  openssl pkeyutl -sign -rawin -inkey "$PRIVATE_KEY" -in "$MANIFEST" -out "$SIGNATURE.bin"
  base64 "$SIGNATURE.bin" | tr -d '\n' > "$SIGNATURE"
  printf '\n' >> "$SIGNATURE"
  rm -f "$SIGNATURE.bin"
}

verify_signature() {
  local public_der signature_bin
  public_der="$(mktemp)"
  signature_bin="$(mktemp)"
  base64 -d "$PUBLIC_KEY_B64" > "$public_der"
  base64 -d "$SIGNATURE" > "$signature_bin"
  openssl pkeyutl -verify -rawin -pubin -inkey "$public_der" -keyform DER -sigfile "$signature_bin" -in "$MANIFEST" >/dev/null
  rm -f "$public_der" "$signature_bin"
}

generate() {
  ensure_keys
  write_manifest
  sign_manifest
  printf 'release manifest: %s\n' "$MANIFEST"
  printf 'manifest sha256: sha256:%s\n' "$(sha256_file "$MANIFEST")"
}

validate() {
  [[ -f "$MANIFEST" ]] || { echo "missing manifest: $MANIFEST" >&2; exit 1; }
  [[ -f "$SIGNATURE" ]] || { echo "missing signature: $SIGNATURE" >&2; exit 1; }
  [[ -f "$PUBLIC_KEY_B64" ]] || { echo "missing public key: $PUBLIC_KEY_B64" >&2; exit 1; }
  verify_signature

  local source_hash config_hash manifest_source manifest_config
  source_hash="sha256:$(hash_paths $(release_source_paths))"
  config_hash="sha256:$(hash_paths $(release_config_paths))"
  manifest_source="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sourceTreeHash"])' "$MANIFEST")"
  manifest_config="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["allowedConfigHash"])' "$MANIFEST")"

  [[ "$source_hash" == "$manifest_source" ]] || { echo "source hash mismatch: $source_hash != $manifest_source" >&2; exit 1; }
  [[ "$config_hash" == "$manifest_config" ]] || { echo "config hash mismatch: $config_hash != $manifest_config" >&2; exit 1; }
  printf 'release snapshot is valid: sha256:%s\n' "$(sha256_file "$MANIFEST")"
}

case "${1:-}" in
  generate) generate ;;
  validate) validate ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
