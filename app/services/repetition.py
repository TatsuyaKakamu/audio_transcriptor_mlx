"""Collapse runs of a phrase that Whisper repeats in a decoding loop."""

import re

MARKER = "[反復省略]"

_MAX_UNIT_CHARS = 40
# Natural speech repeats short words ("はい", "very") and stretches one sound
# ("えーーー", "あははは"), so shorter units need longer runs before we call it a loop.
_MIN_REPEATS_ONE_CHAR = 10
_SHORT_UNIT_CHARS = 4
_MIN_REPEATS_SHORT = 10
_MIN_REPEATS_LONG = 5
# Saying a whole clause three times in a row is rare; Whisper loops do it often.
_PHRASE_CHARS = 10
_MIN_REPEATS_PHRASE = 3
# Units without letters ("、54" or ",9") also loop, but digits and marks repeat in
# real numbers and long dashes, so they need long runs.
_MIN_REPEATS_NON_ALPHA = 10
_MIN_REPEATS_NON_ALPHA_ONE_CHAR = 30
# Prolonged-sound marks count as letters for str.isalpha but carry no word.
_NOT_LETTERS = frozenset("ー〜～")

_RUN = re.compile(r"(\S.{0,%d}?)(?:\s*\1){%d,}" % (_MAX_UNIT_CHARS - 1, _MIN_REPEATS_PHRASE - 1))


def _count_run(text: str, start: int, unit: str) -> tuple[int, int]:
    """Return (repeats, end) of consecutive `unit`s from `start`, allowing whitespace between."""
    count = 0
    end = start
    pos = start
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if not text.startswith(unit, pos):
            return count, end
        count += 1
        pos += len(unit)
        end = pos


def is_loop(unit: str, repeats: int) -> bool:
    unit = unit.strip()
    if not any(ch.isalpha() and ch not in _NOT_LETTERS for ch in unit):
        needed = _MIN_REPEATS_NON_ALPHA_ONE_CHAR if len(unit) == 1 else _MIN_REPEATS_NON_ALPHA
        return repeats >= needed
    if len(unit) == 1:
        needed = _MIN_REPEATS_ONE_CHAR
    elif len(unit) < _SHORT_UNIT_CHARS:
        needed = _MIN_REPEATS_SHORT
    elif len(unit) < _PHRASE_CHARS:
        needed = _MIN_REPEATS_LONG
    else:
        needed = _MIN_REPEATS_PHRASE
    return repeats >= needed


def _truncated_tail(rest: str, unit: str) -> int:
    """Length of `rest` to drop when it is only a cut-off start of `unit` before the text ends."""
    stripped = rest.strip()
    if stripped and len(stripped) < len(unit) and unit.startswith(stripped):
        return len(rest)
    return 0


def collapse_repetitions(text: str) -> str:
    out: list[str] = []
    pos = 0
    search_from = 0
    while True:
        m = _RUN.search(text, search_from)
        if m is None:
            break
        unit = m.group(1)
        repeats, end = _count_run(text, m.start(), unit)
        if not is_loop(unit, repeats):
            # Skip the whole run: rescanning inside it is quadratic on long runs.
            search_from = max(m.start() + 1, end - len(unit) + 1)
            continue
        end += _truncated_tail(text[end:], unit)
        out.append(text[pos:m.start()])
        out.append(unit.strip() + MARKER)
        pos = search_from = end
    out.append(text[pos:])
    return "".join(out)
