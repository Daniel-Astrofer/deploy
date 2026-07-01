#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "flutter-common.sh is a helper. Source it from another script."
  exit 1
fi

kerosene_sudo_user() {
  if [[ "${EUID:-$(id -u)}" -ne 0 || -z "${SUDO_UID:-}" || "${SUDO_UID}" == "0" ]]; then
    return 1
  fi

  local user="${SUDO_USER:-}" uid entry
  if [[ -n "$user" ]]; then
    uid="$(id -u "$user" 2>/dev/null || true)"
    if [[ "$uid" == "$SUDO_UID" ]]; then
      printf '%s\n' "$user"
      return 0
    fi
  fi

  entry="$(getent passwd "$SUDO_UID" 2>/dev/null || true)"
  [[ -n "$entry" ]] || return 1
  printf '%s\n' "${entry%%:*}"
}

kerosene_user_home() {
  local user="$1" entry
  entry="$(getent passwd "$user" 2>/dev/null || true)"
  [[ -n "$entry" ]] || return 1
  entry="${entry#*:*:*:*:*:}"
  printf '%s\n' "${entry%%:*}"
}

kerosene_build_user_home() {
  local user
  user="$(kerosene_sudo_user 2>/dev/null || true)"
  if [[ -n "$user" ]]; then
    kerosene_user_home "$user"
    return
  fi

  printf '%s\n' "${HOME:-}"
}

kerosene_run_as_build_user() {
  local user home
  user="$(kerosene_sudo_user 2>/dev/null || true)"
  if [[ -z "$user" ]]; then
    "$@"
    return
  fi

  home="$(kerosene_user_home "$user" 2>/dev/null || true)"
  [[ -n "$home" ]] || home="/home/$user"

  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$user" -- env HOME="$home" USER="$user" LOGNAME="$user" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$user" HOME="$home" USER="$user" LOGNAME="$user" "$@"
  else
    warn "Cannot drop root privileges for Flutter because runuser/sudo was not found."
    return 127
  fi
}

kerosene_can_build_user_execute() {
  local path="$1"
  if [[ "$path" != */* ]]; then
    kerosene_resolve_build_user_command "$path" >/dev/null 2>&1
    return
  fi

  kerosene_run_as_build_user test -x "$path" >/dev/null 2>&1
}

kerosene_resolve_build_user_command() {
  local command_name="$1" resolved
  if [[ "$command_name" == */* ]]; then
    kerosene_can_build_user_execute "$command_name" || return 1
    printf '%s\n' "$command_name"
    return
  fi

  if [[ -n "$(kerosene_sudo_user 2>/dev/null || true)" ]]; then
    resolved="$(
      kerosene_run_as_build_user bash -lc 'command -v "$1"' bash "$command_name" 2>/dev/null || true
    )"
  else
    resolved="$(command -v "$command_name" 2>/dev/null || true)"
  fi

  [[ -n "$resolved" ]] || return 1
  printf '%s\n' "$resolved"
}

kerosene_resolve_flutter_bin() {
  local frontend_dir="$1" candidate flutter_sdk target_home resolved

  if [[ -n "${FLUTTER_BIN:-}" ]]; then
    resolved="$(kerosene_resolve_build_user_command "$FLUTTER_BIN" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || return 1
    printf '%s\n' "$resolved"
    return
  fi

  if [[ -f "$frontend_dir/android/local.properties" ]]; then
    flutter_sdk="$(awk -F= '$1 == "flutter.sdk" {print $2}' "$frontend_dir/android/local.properties" | tail -n 1)"
    candidate="$flutter_sdk/bin/flutter"
    if [[ -n "$flutter_sdk" ]] && kerosene_can_build_user_execute "$candidate"; then
      printf '%s\n' "$candidate"
      return
    elif [[ -n "$flutter_sdk" ]]; then
      warn "Ignoring Flutter SDK from android/local.properties because the build user cannot execute it: $flutter_sdk"
    fi
  fi

  target_home="$(kerosene_build_user_home 2>/dev/null || true)"
  if [[ -n "$target_home" ]]; then
    candidate="$target_home/flutter/bin/flutter"
    if kerosene_can_build_user_execute "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  fi

  resolved="$(kerosene_resolve_build_user_command flutter 2>/dev/null || true)"
  [[ -n "$resolved" ]] || return 1
  printf '%s\n' "$resolved"
}

kerosene_flutter_sdk_root() {
  local flutter_bin="$1" resolved bin_dir sdk_root
  resolved="$flutter_bin"
  if [[ "$resolved" != */* ]]; then
    resolved="$(command -v "$resolved" 2>/dev/null || true)"
  fi
  [[ -n "$resolved" ]] || return 1

  bin_dir="$(cd "$(dirname "$resolved")" && pwd -P 2>/dev/null)" || return 1
  [[ "$(basename "$bin_dir")" == "bin" ]] || return 1
  sdk_root="$(cd "$bin_dir/.." && pwd -P 2>/dev/null)" || return 1
  [[ -d "$sdk_root/.git" ]] || return 1
  printf '%s\n' "$sdk_root"
}

kerosene_run_flutter() {
  local flutter_bin="$1" sdk_root config_count
  shift

  local env_args=()
  sdk_root="$(kerosene_flutter_sdk_root "$flutter_bin" 2>/dev/null || true)"
  if [[ -n "$sdk_root" ]]; then
    config_count="${GIT_CONFIG_COUNT:-0}"
    [[ "$config_count" =~ ^[0-9]+$ ]] || config_count=0
    env_args+=("GIT_CONFIG_COUNT=$((config_count + 1))")
    env_args+=("GIT_CONFIG_KEY_${config_count}=safe.directory")
    env_args+=("GIT_CONFIG_VALUE_${config_count}=$sdk_root")
  fi

  kerosene_run_as_build_user env "${env_args[@]}" "$flutter_bin" "$@"
}

kerosene_chown_sudo_user() {
  local user owner path
  user="$(kerosene_sudo_user 2>/dev/null || true)"
  [[ -n "$user" && -n "${SUDO_UID:-}" ]] || return 0

  owner="$SUDO_UID:${SUDO_GID:-$(id -g "$user" 2>/dev/null || printf '%s' "$SUDO_UID")}"
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    chown -R "$owner" "$path" 2>/dev/null || true
  done
}
