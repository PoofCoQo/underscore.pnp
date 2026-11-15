#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# The .desktop launcher supplies a terminal, then keeps it open long enough to
# read the outcome. Plain CLI invocations remain non-interactive as before.
if [[ "${1:-}" == "--desktop" ]]; then
    shift
    on_desktop_setup_exit() {
        local setup_exit_code="$?"
        printf '\n'
        if (( setup_exit_code == 0 )); then
            printf 'Setup finished successfully.\n'
        else
            printf 'Setup failed (exit code %s). Review the messages above.\n' \
                "${setup_exit_code}" >&2
        fi
        if [[ -t 0 ]]; then
            read -r -p 'Press Enter to close this window... ' _ || true
        fi
    }
    trap on_desktop_setup_exit EXIT
fi

resolve_env_file() {
    if [[ -n "${PNP_MUSIC_ENV_FILE:-}" ]]; then
        ENV_FILE="${PNP_MUSIC_ENV_FILE}"
    else
        CONFIG_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}"
        ENV_FILE="${CONFIG_ROOT}/underscore.pnp/.env"
    fi
}

prepare_env_file() {
    local env_dir
    env_dir="$(dirname -- "${ENV_FILE}")"
    install -d -m 700 "${env_dir}"
    if [[ ! -e "${ENV_FILE}" ]]; then
        install -m 600 /dev/null "${ENV_FILE}"
    elif [[ ! -f "${ENV_FILE}" ]]; then
        printf 'Secret path exists but is not a regular file: %s\n' \
            "${ENV_FILE}" >&2
        exit 1
    else
        chmod 600 "${ENV_FILE}"
    fi
}

read_secret_masked() {
    local prompt="$1"
    local character=""
    local secret=""
    printf '%s' "${prompt}" >&2
    while IFS= read -r -s -n 1 character; do
        if [[ -z "${character}" ]]; then
            break
        fi
        if [[ "${character}" == $'\177' || "${character}" == $'\b' ]]; then
            if [[ -n "${secret}" ]]; then
                secret="${secret%?}"
                printf '\b \b' >&2
            fi
        else
            secret+="${character}"
            printf '*' >&2
        fi
    done
    printf '\n' >&2
    REPLY="${secret}"
}

configure_discord_token() {
    local force_change="$1"
    local has_token=false
    local discord_token=""

    resolve_env_file
    prepare_env_file
    if grep -Eq \
        '^[[:space:]]*(export[[:space:]]+)?PNP_MUSIC_DISCORD_TOKEN=.+$' \
        "${ENV_FILE}"; then
        has_token=true
    fi

    if [[ "${force_change}" != true && "${has_token}" == true ]]; then
        printf 'Keeping the existing Discord token in %s\n' "${ENV_FILE}"
        return
    fi

    if [[ -t 0 ]]; then
        if [[ "${force_change}" == true && "${has_token}" == true ]]; then
            read_secret_masked \
                'New Discord Bot Token (leave empty to keep the current token): '
        else
            read_secret_masked \
                'Discord Bot Token (leave empty to configure later): '
        fi
        discord_token="${REPLY}"
    else
        discord_token="${PNP_MUSIC_DISCORD_TOKEN:-}"
    fi

    if [[ -n "${discord_token}" ]]; then
        umask 077
        printf 'PNP_MUSIC_DISCORD_TOKEN=%s\n' "${discord_token}" > "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
        printf 'Discord token saved in %s\n' "${ENV_FILE}"
    elif [[ "${has_token}" == true ]]; then
        printf 'Keeping the existing Discord token in %s\n' "${ENV_FILE}"
    else
        printf 'Discord token not set. Add it later to %s\n' "${ENV_FILE}"
    fi
}

if [[ "${1:-}" == "--help" ]]; then
    printf '%s\n' \
        "Usage: ./setup.sh [--install-launcher | --no-launcher | --remove-launcher | --change-token]" \
        "" \
        "Creates .venv, installs underscore.pnp with Discord Voice support," \
        "and creates the protected per-user .env file for the Discord token." \
        "" \
        "  --install-launcher  Install the Linux application menu launcher." \
        "  --no-launcher       Skip launcher installation; leave any existing launcher." \
        "  --remove-launcher   Remove the managed Linux launcher without reinstalling." \
        "  --change-token      Replace only the saved Discord token." \
        "" \
        "On Linux, an interactive setup offers install, keep, or remove." \
        "Without a terminal, the launcher is kept unless --install-launcher is supplied."
    exit 0
fi

if [[ "${1:-}" == "--change-token" ]]; then
    if [[ $# -ne 1 ]]; then
        printf 'The --change-token option takes no additional arguments.\n' >&2
        exit 2
    fi
    configure_discord_token true
    exit 0
fi

if [[ "${1:-}" == "--remove-launcher" ]]; then
    if [[ $# -ne 1 ]]; then
        printf 'The --remove-launcher option takes no additional arguments.\n' >&2
        exit 2
    fi
    if [[ "$(uname -s)" != "Linux" ]]; then
        printf 'The application menu launcher is only installed on Linux.\n' >&2
        exit 1
    fi
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
        printf 'Python 3 is required to remove the application menu launcher.\n' >&2
        exit 1
    fi
    PYTHONPATH="${PROJECT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" \
        "${PYTHON_BIN}" -m pnp_music_player.ui.desktop_integration --remove
    exit $?
fi

LAUNCHER_CHOICE="ask"
if [[ $# -eq 1 && "${1:-}" == "--install-launcher" ]]; then
    LAUNCHER_CHOICE="install"
elif [[ $# -eq 1 && "${1:-}" == "--no-launcher" ]]; then
    LAUNCHER_CHOICE="skip"
elif [[ $# -ne 0 ]]; then
    printf 'Unknown argument: %s\n' "$1" >&2
    printf 'Run ./setup.sh --help for usage.\n' >&2
    exit 2
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    printf 'Python 3 was not found. Install Python 3.11 through 3.15 first.\n' >&2
    exit 1
fi

if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(not (3, 11) <= sys.version_info[:2] < (3, 16))'; then
    printf 'underscore.pnp requires Python 3.11 through 3.15.\n' >&2
    exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    printf 'Creating virtual environment in %s\n' "${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
    printf 'The virtual environment is incomplete. Remove %s and run setup again.\n' \
        "${VENV_DIR}" >&2
    exit 1
fi

if ! "${VENV_PYTHON}" -c 'import sys; raise SystemExit(not (3, 11) <= sys.version_info[:2] < (3, 16))'; then
    printf 'The existing .venv does not use supported Python 3.11 through 3.15.\n' >&2
    printf 'Rename or remove %s and run setup again.\n' "${VENV_DIR}" >&2
    exit 1
fi

printf 'Installing underscore.pnp and Discord Voice dependencies…\n'
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -e "${PROJECT_DIR}[discord]"

if [[ "$(uname -s)" == "Linux" ]]; then
    if [[ "${LAUNCHER_CHOICE}" == "ask" && -t 0 ]]; then
        while true; do
            read -r -p 'Application menu launcher: [i]nstall, [k]eep, [r]emove? [K] ' launcher_answer || launcher_answer=""
            case "${launcher_answer}" in
                i|I|install|INSTALL) LAUNCHER_CHOICE="install"; break ;;
                k|K|keep|KEEP|"") LAUNCHER_CHOICE="skip"; break ;;
                r|R|remove|REMOVE) LAUNCHER_CHOICE="remove"; break ;;
                *) printf 'Please answer i, k, or r.\n' ;;
            esac
        done
    fi
    if [[ "${LAUNCHER_CHOICE}" == "install" ]]; then
        if ! "${VENV_PYTHON}" -m pnp_music_player.ui.desktop_integration "${PROJECT_DIR}"; then
            printf 'Warning: the Linux player launcher could not be installed.\n' >&2
        fi
    elif [[ "${LAUNCHER_CHOICE}" == "remove" ]]; then
        "${VENV_PYTHON}" -m pnp_music_player.ui.desktop_integration --remove
    else
        printf 'Skipping application menu launcher (any existing launcher is unchanged).\n'
    fi
fi

configure_discord_token false

if ! "${VENV_PYTHON}" -c \
    'import ctypes.util, sys; sys.exit(0 if ctypes.util.find_library("opus") else 1)'; then
    printf '%s\n' \
        'Warning: libopus was not found. Discord Voice needs the native Opus library.' \
        'Debian/Ubuntu: sudo apt install libopus0' \
        'Fedora/Nobara: sudo dnf install opus' >&2
fi

printf '\nSetup complete. Start the player with:\n  %s/start.sh\n' "${PROJECT_DIR}"
