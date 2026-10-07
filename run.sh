#!/usr/bin/env bash
# Tri-Camera IMX708 Monitor Launcher
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

python3 "$DIR/camera_monitor.py" "$@"
