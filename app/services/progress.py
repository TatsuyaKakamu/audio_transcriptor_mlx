from __future__ import annotations

from typing import Callable

from app.services import notifier

_MILESTONES = (25, 50, 75)


def _fmt_sec(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def make_milestone_callback(filename: str) -> Callable[[int, int, float], None]:
    fired: set[int] = set()

    def cb(processed: int, total: int, elapsed: float) -> None:
        if total <= 0:
            return
        pct = int(100 * processed / total)
        crossed = {m for m in _MILESTONES if pct >= m and m not in fired}
        if not crossed:
            return
        fired.update(crossed)
        highest = max(crossed)
        if elapsed > 0 and processed > 0:
            eta_sec = (total - processed) / (processed / elapsed)
            body = f"{filename}  残り {_fmt_sec(eta_sec)}"
        else:
            body = filename
        notifier.notify(f"文字起こし {highest}%", body)

    return cb
