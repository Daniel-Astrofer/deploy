#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$ROOT"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "$ROOT/infra/scripts/polyrepo-env.sh"
CONTRACT="$ROOT/infra/docker/images.yaml"
IMAGE_KEY="${1:-}"

if [[ -z "$IMAGE_KEY" || "$IMAGE_KEY" == "-h" || "$IMAGE_KEY" == "--help" ]]; then
  cat <<'USAGE'
Usage: bash infra/docker/build-image.sh <image-key>

Image keys are defined in infra/docker/images.yaml.
Common keys: server, kfe-service, kerosene-vault, kerosene-node,
kerosene-rsctl, kerosene-jctl, tor, web-page.

This script reads the image contract and resolves build contexts from the
independent polyrepo workspace.
USAGE
  exit 0
fi

python3 - \
  "$CONTRACT" \
  "$IMAGE_KEY" \
  "$ROOT" \
  "$CORE_DIR" \
  "$CLIENTS_DIR" \
  "$VAULT_DIR" \
  "$NODE_DIR" \
  "$CONTRACTS_DIR" \
  "$ADMIN_DIR" \
  "$KFE_DIR" \
  "$SHARED_DIR" <<'PY'
import subprocess
import sys
from pathlib import Path

contract, key, root, core, clients, vault, node, contracts, admin, kfe, shared = sys.argv[1:]
text = Path(contract).read_text(encoding="utf-8").splitlines()

current = None
items = {}
for raw in text:
    line = raw.rstrip()
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
        current = line.strip()[:-1]
        items[current] = {}
        continue
    if current and line.startswith("    ") and ":" in line:
        name, value = line.strip().split(":", 1)
        items[current][name] = value.strip().strip('"').strip("'")

if key not in items:
    print(f"Unknown image key: {key}", file=sys.stderr)
    print("Known keys: " + ", ".join(sorted(items)), file=sys.stderr)
    sys.exit(2)

item = items[key]
required = ["image", "local_tag", "dockerfile", "context_repository", "context"]
missing = [field for field in required if not item.get(field)]
if missing:
    print(f"Image {key} is missing fields: {', '.join(missing)}", file=sys.stderr)
    sys.exit(2)

if item["dockerfile"].startswith("generated-by-"):
    print(f"Image {key} does not have a real Dockerfile yet: {item['dockerfile']}", file=sys.stderr)
    sys.exit(3)

root_path = Path(root)
dockerfile = root_path / item["dockerfile"]
repository_roots = {
    "deploy": root_path,
    "core": Path(core),
    "clients": Path(clients),
    "vault": Path(vault),
    "node": Path(node),
    "contracts": Path(contracts),
    "admin": Path(admin),
    "kfe": Path(kfe),
    "shared": Path(shared),
}
context_repository = item["context_repository"]
if context_repository == "generated":
    print(
        f"Image {key} requires its dedicated orchestration builder.",
        file=sys.stderr,
    )
    sys.exit(3)
if context_repository not in repository_roots:
    print(
        f"Image {key} has an unknown context repository: {context_repository}",
        file=sys.stderr,
    )
    sys.exit(2)
context = repository_roots[context_repository] / item["context"]
if not dockerfile.is_file():
    print(f"Dockerfile not found: {dockerfile}", file=sys.stderr)
    sys.exit(4)
if not context.exists():
    print(f"Build context not found: {context}", file=sys.stderr)
    sys.exit(4)

image = f"{item['image']}:{item['local_tag']}"
cmd = ["docker", "build"]
if context_repository == "core":
    cmd.extend(["--build-context", f"contracts={contracts}"])
    cmd.extend(["--build-context", f"deploy={root}"])
    cmd.extend(["--build-context", f"shared={shared}"])
elif context_repository == "kfe":
    cmd.extend(["--build-context", f"contracts={contracts}"])
    cmd.extend(["--build-context", f"deploy={root}"])
    cmd.extend(["--build-context", f"shared={shared}"])
cmd.extend(["-t", image, "-f", str(dockerfile), str(context)])
print(" ".join(cmd))
subprocess.check_call(cmd)
PY
