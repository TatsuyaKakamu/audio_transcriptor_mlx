#!/usr/bin/env bash
# Uninstall the mlx-audio-transcriptor launchd watcher.

set -euo pipefail

LABEL="com.mlx-audio-transcriptor.watcher"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true

if [[ -f "${PLIST_DEST}" ]]; then
    rm -f "${PLIST_DEST}"
    echo "removed: ${PLIST_DEST}"
else
    echo "not installed: ${PLIST_DEST}"
fi

echo "done."
