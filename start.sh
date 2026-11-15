#!/usr/bin/env bash

set -euo pipefail

# Include shell, interpreter and imports in the first-frame startup metric.
export PNP_MUSIC_LAUNCH_EPOCH_NS="$(date +%s%N)"

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
    printf 'underscore.pnp is not set up yet. Run %s/setup.sh first.\n' \
        "${PROJECT_DIR}" >&2
    exit 1
fi

cd -- "${PROJECT_DIR}"

# Run the splash independently so it can paint while the main process imports.
# Keep this shell alive to clean it up if startup fails before the first frame.
if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]] && \
   [[ "${PNP_MUSIC_NO_SPLASH:-}" != "1" ]]; then
    "${VENV_PYTHON}" -I \
        "${PROJECT_DIR}/src/pnp_music_player/ui/launcher_splash.py" &
    splash_pid=$!
    export PNP_MUSIC_SPLASH_PID="${splash_pid}"
    export PNP_MUSIC_LAUNCHER_PID="$$"
    cleanup_splash() {
        kill "${splash_pid}" 2>/dev/null || true
        wait "${splash_pid}" 2>/dev/null || true
    }
    trap cleanup_splash EXIT
fi

"${VENV_PYTHON}" -m pnp_music_player "$@"
