#!/usr/bin/env bash
set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/scripts/common.sh
source "$LOCAL_SCRIPT_DIR/../common.sh"

MINUTES="${KEROSENE_CAPTURE_MINUTES:-20}"
RAW=0
TAIL="${KEROSENE_CAPTURE_TAIL:-20000}"
OUTPUT=""
START_ARGS=()

usage() {
  cat <<'EOF'
Usage: infra/scripts/local/capture-logs.sh [options] [-- start-options...]

Stops the local Kerosene infra, starts it again through the canonical control
entrypoint, and captures logs into a timestamped .txt file for startup diagnostics.

Options:
  --minutes N      Capture duration in minutes. Must be between 10 and 30.
  --output FILE    Destination .txt file. Defaults to logs/local-capture/<timestamp>.txt.
  --raw            Capture raw logs instead of filtered logs.
  --tail N         Initial docker log tail. Defaults to KEROSENE_CAPTURE_TAIL or 20000.
  -h, --help       Show this help.

Examples:
  infra/scripts/local/capture-logs.sh --minutes 10 -- --no-build
  infra/scripts/local/capture-logs.sh --minutes 30 --raw --output /tmp/kerosene-startup.txt
EOF
}

is_number() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minutes)
      shift
      [[ $# -gt 0 ]] || fail "--minutes requires a number."
      MINUTES="$1"
      ;;
    --output)
      shift
      [[ $# -gt 0 ]] || fail "--output requires a file path."
      OUTPUT="$1"
      ;;
    --raw)
      RAW=1
      ;;
    --tail)
      shift
      [[ $# -gt 0 ]] || fail "--tail requires a number."
      TAIL="$1"
      ;;
    --)
      shift
      START_ARGS+=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

is_number "$MINUTES" || fail "--minutes must be numeric."
is_number "$TAIL" || fail "--tail must be numeric."
if (( MINUTES < 10 || MINUTES > 30 )); then
  fail "--minutes must be between 10 and 30."
fi

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$REPO_ROOT/logs/local-capture/kerosene-local-$(date +%Y%m%d-%H%M%S).txt"
fi

mkdir -p "$(dirname "$OUTPUT")"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kerosene-capture.XXXXXX")"
STOP_LOG="$TMP_DIR/01-stop.log"
START_LOG="$TMP_DIR/02-start.log"
DOCKER_LOG="$TMP_DIR/03-docker.log"
POSTMORTEM_LOG="$TMP_DIR/04-postmortem.log"
META_LOG="$TMP_DIR/00-meta.log"

append_section() {
  local title="$1"
  local file="$2"
  {
    printf '\n===== %s =====\n' "$title"
    if [[ -s "$file" ]]; then
      cat "$file"
    else
      printf '(empty)\n'
    fi
  } >> "$OUTPUT"
}

assemble_output() {
  local status="${1:-0}"
  {
    printf 'Kerosene local log capture\n'
    printf 'Generated at: %s\n' "$(date -Is)"
    printf 'Repository: %s\n' "$REPO_ROOT"
    printf 'Duration requested: %s minutes\n' "$MINUTES"
    printf 'log mode: %s\n' "$([[ "$RAW" -eq 1 ]] && printf raw || printf filtered)"
    printf 'log tail: %s\n' "$TAIL"
    printf 'start args:'
    if [[ "${#START_ARGS[@]}" -eq 0 ]]; then
      printf ' (none)'
    else
      printf ' %q' "${START_ARGS[@]}"
    fi
    printf '\n'
    printf 'Script exit status: %s\n' "$status"
  } > "$OUTPUT"

  append_section "capture metadata" "$META_LOG"
  append_section "stop output" "$STOP_LOG"
  append_section "start output" "$START_LOG"
  append_section "logs output" "$DOCKER_LOG"
  append_section "postmortem raw logs" "$POSTMORTEM_LOG"
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  assemble_output "$status" || true
  rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT INT TERM

CAPTURE_SECONDS=$((MINUTES * 60))
CAPTURE_END=$(( $(date +%s) + CAPTURE_SECONDS ))
LOG_ARGS=(--tail "$TAIL")
if [[ "$RAW" -eq 1 ]]; then
  LOG_ARGS=(--raw "${LOG_ARGS[@]}")
fi

{
  printf '[capture] hostname: %s\n' "$(hostname 2>/dev/null || printf unknown)"
  printf '[capture] user: %s\n' "$(id 2>/dev/null || printf unknown)"
  printf '[capture] git HEAD: %s\n' "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)"
  printf '[capture] docker client:\n'
  docker version --format '{{.Client.Version}}' 2>/dev/null || true
} >> "$META_LOG" 2>&1

info "Writing final capture to $OUTPUT"
info "Stopping current local infra..."
if ! bash "$LOCAL_SCRIPT_DIR/control.sh" stop > "$STOP_LOG" 2>&1; then
  fail "local stop failed. See $STOP_LOG"
fi

capture_logs() {
  local remaining status started_at elapsed
  while true; do
    remaining=$((CAPTURE_END - $(date +%s)))
    if (( remaining <= 0 )); then
      return 0
    fi

    printf '\n[capture] starting log capture for up to %ss at %s\n' "$remaining" "$(date -Is)" >> "$DOCKER_LOG"
    started_at="$(date +%s)"
    set +e
    timeout --preserve-status "${remaining}s" bash "$LOCAL_SCRIPT_DIR/control.sh" logs "${LOG_ARGS[@]}" >> "$DOCKER_LOG" 2>&1
    status=$?
    set -e
    elapsed=$(( $(date +%s) - started_at ))

    case "$status" in
      124|130|143)
        return 0
        ;;
      0)
        printf '[capture] logs exited after %ss while capture window is still open; retrying.\n' "$elapsed" >> "$DOCKER_LOG"
        sleep 2
        ;;
      *)
        printf '[capture] logs exited with status %s; retrying while startup continues.\n' "$status" >> "$DOCKER_LOG"
        sleep 2
        ;;
    esac
  done
}

info "Starting log capture and local infra..."
capture_logs &
LOG_PID=$!

set +e
bash "$LOCAL_SCRIPT_DIR/control.sh" start "${START_ARGS[@]}" > "$START_LOG" 2>&1
START_STATUS=$?
set -e

if [[ "$START_STATUS" -ne 0 ]]; then
  printf '[capture] start exited with status %s at %s\n' "$START_STATUS" "$(date -Is)" >> "$META_LOG"
  {
    printf '[capture] start failed; collecting raw no-follow logs at %s\n' "$(date -Is)"
    bash "$LOCAL_SCRIPT_DIR/control.sh" logs --raw --no-follow --tail "$TAIL" || true
  } >> "$POSTMORTEM_LOG" 2>&1
else
  printf '[capture] start completed successfully at %s\n' "$(date -Is)" >> "$META_LOG"
fi

info "Waiting for remaining log capture window (${MINUTES} minutes total)..."
wait "$LOG_PID" || true

if [[ "$START_STATUS" -ne 0 ]]; then
  fail "local start failed with status $START_STATUS. Capture written to $OUTPUT"
fi

info "Capture completed: $OUTPUT"
