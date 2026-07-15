#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bash control.sh logs kerosene-app-ch "$@"
