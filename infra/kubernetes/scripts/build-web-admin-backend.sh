#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/backend-common.sh
source "$SCRIPT_DIR/backend-common.sh"
# shellcheck source=scripts/flutter-common.sh
source "$SCRIPT_DIR/flutter-common.sh"

BUILD_JAR=1
FRONTEND_DIR="$REPO_ROOT/frontend"
FRONTEND_BUILD_DIR="$FRONTEND_DIR/build/web"
BACKEND_WEB_ADMIN_BUILD_DIR="$BACKEND_DIR/web-admin-build"
FRONTEND_LOG_DIR="$FRONTEND_DIR/logs"
FRONTEND_BUILD_LOG_FILE="$FRONTEND_LOG_DIR/backend-embedded-web-build.log"

usage() {
  cat <<'EOF'
Usage: scripts/build-web-admin-backend.sh [options]

Builds the Flutter web admin for same-origin Tor deployment, copies it into
backend/kerosene/web-admin-build, and optionally packages the Spring Boot jar.

Options:
  --no-jar    Only build/copy the Flutter bundle; do not run Gradle bootJar.
  -h, --help  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-jar) BUILD_JAR=0 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
  shift
done

[[ -f "$FRONTEND_DIR/pubspec.yaml" ]] || fail "Frontend pubspec not found at $FRONTEND_DIR/pubspec.yaml"
FLUTTER_BIN="$(kerosene_resolve_flutter_bin "$FRONTEND_DIR")" || fail "Flutter CLI not found for the non-root build user."

if [[ -f "$ENV_FILE" ]]; then
  load_backend_env
fi

FRONTEND_PASSKEY_RP_ID="${FRONTEND_PASSKEY_RP_ID:-${WEBAUTHN_RP_ID:-kerosene-device}}"
FRONTEND_PASSKEY_ORIGIN="${FRONTEND_PASSKEY_ORIGIN:-android:apk-key-hash:kerosene}"
FRONTEND_API_URL="${FRONTEND_API_URL:-}"

mkdir -p "$FRONTEND_LOG_DIR" "$BACKEND_WEB_ADMIN_BUILD_DIR"
kerosene_chown_sudo_user "$FRONTEND_DIR/.dart_tool" "$FRONTEND_DIR/build" "$FRONTEND_LOG_DIR"

WEB_API_DEFINE=()
if [[ -n "$FRONTEND_API_URL" ]]; then
  WEB_API_DEFINE+=(--dart-define="WEB_API_URL=$FRONTEND_API_URL")
  info "Building Flutter web admin for API $FRONTEND_API_URL."
else
  info "Building Flutter web admin for backend-served same-origin onion access."
fi
(
  cd "$FRONTEND_DIR"
  FLUTTER_BUILD_ARGS=(web --release --csp --no-web-resources-cdn --target lib/web_main.dart)
  if [[ "${FLUTTER_BUILD_NO_PUB:-0}" == "1" ]]; then
    FLUTTER_BUILD_ARGS+=(--no-pub)
  fi
  kerosene_run_flutter "$FLUTTER_BIN" build "${FLUTTER_BUILD_ARGS[@]}" \
    "${WEB_API_DEFINE[@]}" \
    --dart-define="PASSKEY_RP_ID=$FRONTEND_PASSKEY_RP_ID" \
    --dart-define="PASSKEY_ORIGIN=$FRONTEND_PASSKEY_ORIGIN"
) > "$FRONTEND_BUILD_LOG_FILE" 2>&1 || {
  kerosene_chown_sudo_user "$FRONTEND_DIR/.dart_tool" "$FRONTEND_DIR/build" "$FRONTEND_LOG_DIR"
  tail -n 100 "$FRONTEND_BUILD_LOG_FILE" >&2 || true
  fail "Flutter web build failed. See $FRONTEND_BUILD_LOG_FILE"
}

[[ -f "$FRONTEND_BUILD_DIR/index.html" ]] || fail "Flutter build did not produce $FRONTEND_BUILD_DIR/index.html"
rm -f "$FRONTEND_BUILD_DIR/kerosene-runtime-config.json"

rm -rf -- "$BACKEND_WEB_ADMIN_BUILD_DIR"
mkdir -p "$BACKEND_WEB_ADMIN_BUILD_DIR"
cp -R "$FRONTEND_BUILD_DIR"/. "$BACKEND_WEB_ADMIN_BUILD_DIR"/
: > "$BACKEND_WEB_ADMIN_BUILD_DIR/.gitkeep"
kerosene_chown_sudo_user "$FRONTEND_DIR/.dart_tool" "$FRONTEND_DIR/build" "$FRONTEND_LOG_DIR" "$BACKEND_WEB_ADMIN_BUILD_DIR"
info "Copied web admin bundle to $BACKEND_WEB_ADMIN_BUILD_DIR"

if [[ "$BUILD_JAR" -eq 1 ]]; then
  if [[ -n "${KEROSENE_JAVA_HOME:-}" ]]; then
    export JAVA_HOME="$KEROSENE_JAVA_HOME"
  elif [[ -d /usr/lib/jvm/java-21-openjdk ]]; then
    export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
  fi

  info "Packaging backend jar with embedded web admin."
  (
    cd "$BACKEND_DIR"
    ./gradlew bootJar -x test --max-workers="${GRADLE_MAX_WORKERS:-2}"
  )
fi
