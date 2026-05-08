from __future__ import annotations

import subprocess


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str) -> None:
    script = (
        f'display notification "{_escape(message)}" '
        f'with title "{_escape(title)}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
