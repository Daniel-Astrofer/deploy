#!/usr/bin/env bash
# Move Docker's data-root off the tiny /var partition onto a capped volume under /home.
# Requires sudo. Stops Docker/containerd briefly; kind nodes restart with Docker.
#
# Default: fixed-size loop filesystem (hard cap). Docker cannot grow past --size.
# Without a cap, image/build cache churn can fill all of /home unnoticed.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo bash infra/scripts/relocate-docker-data-root.sh [options]

Relocates Docker data-root so Gradle/image builds are not limited by a small /var,
while keeping a hard disk budget so Docker cannot silently fill /home.

Options:
  --size SIZE          Max Docker volume size (default: 40G). Examples: 30G, 40G, 50G
  --mount-dir DIR      Where the capped volume is mounted (default:
                       /home/<user>/.local/share/docker-data)
  --image-file PATH    Loop-backed sparse file (default: <mount-dir>.img)
  --raw-dir DIR        UNSAFE: use a plain directory with no size cap (not recommended)
  --builder-cache SIZE BuildKit GC budget inside the volume (default: 12GB)
  -h, --help           Show this help

Why:
  /var is a separate ~12G partition. Docker + kind already need most of it;
  multi-stage Gradle bootJar then fails with "No space left on device".
  Moving to /home without a cap is also bad: dangling images and build cache
  can grow until the home filesystem is full.

What you get with the default (capped loop volume):
  - Hard limit: Docker sees only a SIZE-sized filesystem (df shows it)
  - BuildKit auto-GC: builder cache kept under --builder-cache
  - Survives reboot via /etc/fstab loop entry

After relocate:
  df -h <mount-dir>
  docker system df
  bash infra/kubernetes/scripts/import-local-docker-images.sh
  bash infra/deploy.sh --wait

Manual reclaim inside the budget:
  docker builder prune -af
  docker image prune -af   # drops unused images only
  docker system df
USAGE
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "[!] Run as root: sudo bash $0 ..." >&2
  exit 1
fi

REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
  REAL_USER="$(getent passwd 1000 | cut -d: -f1 || true)"
fi
REAL_HOME="$(getent passwd "${REAL_USER:-root}" | cut -d: -f6)"
REAL_HOME="${REAL_HOME:-${HOME:-}}"
[[ -n "$REAL_HOME" ]] || {
  echo "REAL_HOME or HOME must be set." >&2
  exit 1
}

SIZE="40G"
MOUNT_DIR="$REAL_HOME/.local/share/docker-data"
IMAGE_FILE=""
RAW_DIR=""
BUILDER_CACHE="12GB"
SRC="/var/lib/docker"
DAEMON_JSON="/etc/docker/daemon.json"
FSTAB="/etc/fstab"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --size)
      shift
      [[ $# -gt 0 ]] || { echo "[!] --size requires a value" >&2; exit 2; }
      SIZE="$1"
      ;;
    --mount-dir)
      shift
      [[ $# -gt 0 ]] || { echo "[!] --mount-dir requires a value" >&2; exit 2; }
      MOUNT_DIR="$1"
      ;;
    --image-file)
      shift
      [[ $# -gt 0 ]] || { echo "[!] --image-file requires a value" >&2; exit 2; }
      IMAGE_FILE="$1"
      ;;
    --raw-dir)
      shift
      [[ $# -gt 0 ]] || { echo "[!] --raw-dir requires a value" >&2; exit 2; }
      RAW_DIR="$1"
      ;;
    --builder-cache)
      shift
      [[ $# -gt 0 ]] || { echo "[!] --builder-cache requires a value" >&2; exit 2; }
      BUILDER_CACHE="$1"
      ;;
    *)
      echo "[!] Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -n "$RAW_DIR" && ( -n "$IMAGE_FILE" || "$MOUNT_DIR" != "$REAL_HOME/.local/share/docker-data" ) ]]; then
  # allow raw-dir alone; if user set both raw and custom mount, raw wins as data-root
  :
fi

if [[ -z "$IMAGE_FILE" ]]; then
  IMAGE_FILE="${MOUNT_DIR}.img"
fi

TARGET=""
MODE=""
if [[ -n "$RAW_DIR" ]]; then
  MODE="raw"
  TARGET="$RAW_DIR"
  echo "[!] WARNING: --raw-dir has NO hard size limit. Docker can fill the parent filesystem." >&2
else
  MODE="loop"
  TARGET="$MOUNT_DIR"
fi

echo "[*] Mode:            $MODE"
echo "[*] Current /var:    $(df -h /var | awk 'NR==2{print $3" used / "$2" total, "$4" free"}')"
echo "[*] Current /home:   $(df -h /home | awk 'NR==2{print $3" used / "$2" total, "$4" free"}')"
echo "[*] Docker SRC:      $SRC"
echo "[*] New data-root:   $TARGET"
if [[ "$MODE" == "loop" ]]; then
  echo "[*] Hard cap size:   $SIZE (loop file $IMAGE_FILE)"
fi
echo "[*] Builder GC keep: $BUILDER_CACHE"

if [[ -f "$DAEMON_JSON" ]] && grep -q '"data-root"' "$DAEMON_JSON"; then
  echo "[!] $DAEMON_JSON already sets data-root. Inspect and adjust manually." >&2
  cat "$DAEMON_JSON" >&2
  exit 1
fi

# --- prepare target filesystem -------------------------------------------------
if [[ "$MODE" == "loop" ]]; then
  mkdir -p "$(dirname "$IMAGE_FILE")" "$MOUNT_DIR"

  if findmnt -n "$MOUNT_DIR" >/dev/null 2>&1; then
    echo "[!] $MOUNT_DIR is already a mount point. Unmount or choose another --mount-dir." >&2
    findmnt "$MOUNT_DIR" >&2 || true
    exit 1
  fi

  if [[ -e "$MOUNT_DIR" && -n "$(ls -A "$MOUNT_DIR" 2>/dev/null || true)" ]]; then
    echo "[!] Mount dir exists and is not empty: $MOUNT_DIR" >&2
    exit 1
  fi

  if [[ -e "$IMAGE_FILE" ]]; then
    echo "[!] Loop image already exists: $IMAGE_FILE" >&2
    echo "[!] Remove it only if you are sure, then re-run." >&2
    exit 1
  fi

  echo "[*] Creating sparse loop image $IMAGE_FILE ($SIZE hard cap)"
  # truncate creates a sparse file: does not pre-allocate full SIZE on disk until used
  truncate -s "$SIZE" "$IMAGE_FILE"
  mkfs.ext4 -F -L kerosene-docker "$IMAGE_FILE"

  echo "[*] Mounting loop image on $MOUNT_DIR"
  mount -o loop "$IMAGE_FILE" "$MOUNT_DIR"

  # Persistent mount across reboots
  fstab_line="$IMAGE_FILE $MOUNT_DIR ext4 loop,defaults,nofail 0 2"
  if ! grep -Fq "$IMAGE_FILE" "$FSTAB"; then
    echo "[*] Adding fstab entry for reboot persistence"
    printf '\n# Kerosene: capped Docker data-root (hard size limit)\n%s\n' "$fstab_line" >>"$FSTAB"
  else
    echo "[*] fstab already mentions $IMAGE_FILE; leaving as-is"
  fi
else
  mkdir -p "$(dirname "$TARGET")"
  if [[ -e "$TARGET" && -n "$(ls -A "$TARGET" 2>/dev/null || true)" ]]; then
    echo "[!] Target exists and is not empty: $TARGET" >&2
    exit 1
  fi
  mkdir -p "$TARGET"
fi

# --- stop docker and move existing data ---------------------------------------
echo "[*] Stopping docker / containerd"
systemctl stop docker.socket docker 2>/dev/null || true
systemctl stop containerd 2>/dev/null || true

if [[ -d "$SRC" && ! -L "$SRC" ]]; then
  used="$(du -sh "$SRC" 2>/dev/null | awk '{print $1}')"
  echo "[*] Moving existing Docker data ($used) -> $TARGET"
  if command -v rsync >/dev/null 2>&1; then
    rsync -aHAX --info=progress2 "$SRC"/ "$TARGET"/
    backup="${SRC}.pre-relocate.$(date +%Y%m%d%H%M%S)"
    mv "$SRC" "$backup"
    echo "[*] Old tree kept at $backup (delete after you confirm Docker works)"
  else
    # TARGET must be empty dir for mv contents
    shopt -s dotglob
    mv "$SRC"/* "$TARGET"/ 2>/dev/null || true
    shopt -u dotglob
    rmdir "$SRC" 2>/dev/null || mv "$SRC" "${SRC}.pre-relocate.$(date +%Y%m%d%H%M%S)"
  fi
elif [[ -L "$SRC" ]]; then
  echo "[!] $SRC is already a symlink: $(readlink -f "$SRC" || readlink "$SRC")" >&2
  exit 1
else
  echo "[*] No existing $SRC directory; starting with empty data-root"
fi

# --- daemon.json: data-root + BuildKit GC budget ------------------------------
mkdir -p /etc/docker
write_daemon_json() {
  local root="$1"
  local keep="$2"
  if command -v jq >/dev/null 2>&1 && [[ -f "$DAEMON_JSON" ]]; then
    local tmp
    tmp="$(mktemp)"
    jq --arg root "$root" --arg keep "$keep" '
      . + {
        "data-root": $root,
        "builder": ((.builder // {}) + {
          "gc": {
            "enabled": true,
            "defaultKeepStorage": $keep
          }
        })
      }
    ' "$DAEMON_JSON" >"$tmp"
    mv "$tmp" "$DAEMON_JSON"
  else
    cat >"$DAEMON_JSON" <<EOF
{
  "data-root": "$root",
  "builder": {
    "gc": {
      "enabled": true,
      "defaultKeepStorage": "$keep"
    }
  }
}
EOF
  fi
}

write_daemon_json "$TARGET" "$BUILDER_CACHE"
echo "[*] Wrote $DAEMON_JSON:"
cat "$DAEMON_JSON"

echo "[*] Starting containerd / docker"
systemctl start containerd
systemctl start docker

echo
echo "[*] Docker Root Dir:"
docker info 2>/dev/null | grep -E 'Docker Root Dir|Storage Driver' || true
echo "[*] Capped volume usage (hard limit):"
if [[ "$MODE" == "loop" ]]; then
  df -h "$MOUNT_DIR" || true
else
  df -h "$TARGET" || true
fi
echo "[*] Host /var (should be much freer):"
df -h /var || true
echo "[*] Host /home:"
df -h /home || true

echo
echo "[+] Done."
echo "[+] Hard cap: Docker cannot use more than the volume size ($SIZE in loop mode)."
echo "[+] BuildKit keeps at most ~$BUILDER_CACHE of build cache (auto GC)."
echo "[+] Check anytime:  df -h $TARGET && docker system df"
echo "[+] Kind may need:  docker start kerosene-local-control-plane"
echo "[+] Then rebuild:   bash infra/kubernetes/scripts/import-local-docker-images.sh"
