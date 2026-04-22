#!/usr/bin/env bash
# Install launchd LaunchAgent that watches the configured directory and runs
# `python -m app.cli scan` whenever it changes.
#
# Usage:  ./scripts/install-watcher.sh
# Requires a virtualenv at <project root>/.venv with the project deps installed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
TEMPLATE="${SCRIPT_DIR}/com.mlx-audio-transcriptor.watcher.plist.template"

LABEL="com.mlx-audio-transcriptor.watcher"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

CONFIG_DIR="${HOME}/.config/mlx-audio-transcriptor"
CONFIG_PATH="${CONFIG_DIR}/config.toml"
LOG_DIR="${HOME}/Library/Logs/mlx-audio-transcriptor"

if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "error: ${VENV_PYTHON} not found or not executable." >&2
    echo "       Create the venv first: python -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -f "${TEMPLATE}" ]]; then
    echo "error: template not found at ${TEMPLATE}" >&2
    exit 1
fi

mkdir -p "${LOG_DIR}" "${CONFIG_DIR}" "${HOME}/Library/LaunchAgents"

if [[ ! -f "${CONFIG_PATH}" && -f "${PROJECT_ROOT}/config.toml.example" ]]; then
    cp "${PROJECT_ROOT}/config.toml.example" "${CONFIG_PATH}"
    echo "installed default config: ${CONFIG_PATH}"
fi

# Resolve watch_dir from config if present; otherwise default to ~/Downloads.
WATCH_DIR="${HOME}/Downloads"
if [[ -f "${CONFIG_PATH}" ]]; then
    RESOLVED="$("${VENV_PYTHON}" -c "from app.config import load_config; print(load_config().watch_dir)" 2>/dev/null || true)"
    if [[ -n "${RESOLVED}" ]]; then
        WATCH_DIR="${RESOLVED}"
    fi
fi

echo "project root: ${PROJECT_ROOT}"
echo "venv python:  ${VENV_PYTHON}"
echo "watch dir:    ${WATCH_DIR}"
echo "plist dest:   ${PLIST_DEST}"

# Substitute placeholders. Use | as sed delimiter to avoid clashing with path slashes.
sed \
    -e "s|__VENV_PYTHON__|${VENV_PYTHON}|g" \
    -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
    -e "s|__WATCH_DIR__|${WATCH_DIR}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "${TEMPLATE}" > "${PLIST_DEST}"

echo "wrote: ${PLIST_DEST}"

# Reload the agent. Use bootout + bootstrap so re-running the script replaces
# any previous registration.
UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "${PLIST_DEST}"
launchctl enable "gui/${UID_NUM}/${LABEL}"

echo "loaded LaunchAgent: ${LABEL}"
echo
echo "check status: launchctl print gui/${UID_NUM}/${LABEL} | head"
echo "logs:         tail -f ${LOG_DIR}/stderr.log"
