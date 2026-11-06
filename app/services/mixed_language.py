"""Pure helpers for the ja/en mixed-language mode.

The flow is: split speech intervals so none exceeds Whisper's 30 s window,
detect ja/en per interval, resolve unreliable detections by inheriting the
previous language, then group consecutive same-language intervals so each
group is transcribed with a fixed language.
"""

import numpy as np

CANDIDATE_LANGUAGES: tuple[str, ...] = ("ja", "en")
DEFAULT_LANGUAGE = "ja"
MIN_DETECT_SEC = 2.0

Interval = tuple[float, float]


def split_long_intervals(
    audio: np.ndarray,
    intervals: list[Interval],
    *,
    sample_rate: int = 16_000,
    max_sec: float = 30.0,
    frame_sec: float = 0.1,
) -> list[Interval]:
    """Split intervals longer than max_sec at the quietest frame.

    The cut is searched in the second half of each max_sec window so pieces
    stay reasonably long; cutting at the lowest-RMS frame avoids splitting words.
    """
    result: list[Interval] = []
    frame = max(1, int(frame_sec * sample_rate))
    for start, end in intervals:
        while end - start > max_sec:
            lo = int((start + max_sec / 2) * sample_rate)
            hi = int((start + max_sec) * sample_rate)
            window = audio[lo:hi]
            n_frames = len(window) // frame
            if n_frames == 0:
                cut = start + max_sec
            else:
                frames = window[: n_frames * frame].reshape(n_frames, frame)
                rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
                quietest = int(np.argmin(rms))
                cut = (lo + quietest * frame + frame // 2) / sample_rate
            result.append((start, cut))
            start = cut
        result.append((start, end))
    return result


def resolve_languages(
    durations: list[float],
    probs: list[dict[str, float] | None],
    *,
    min_sec: float = MIN_DETECT_SEC,
    min_margin: float = 0.2,
) -> list[str]:
    """Pick ja/en per interval; unreliable intervals inherit the previous language.

    An interval is unreliable when it is shorter than min_sec, detection failed
    (None), or the normalized ja/en probability gap is below min_margin. Leading
    unreliable intervals take the first reliable language.
    """
    decided: list[str | None] = []
    for dur, p in zip(durations, probs):
        if p is None or dur < min_sec:
            decided.append(None)
            continue
        scores = {lang: float(p.get(lang, 0.0)) for lang in CANDIDATE_LANGUAGES}
        total = sum(scores.values())
        if total <= 0.0:
            decided.append(None)
            continue
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        margin = (ranked[0][1] - ranked[1][1]) / total
        decided.append(ranked[0][0] if margin >= min_margin else None)

    first = next((lang for lang in decided if lang is not None), DEFAULT_LANGUAGE)
    resolved: list[str] = []
    current = first
    for lang in decided:
        if lang is not None:
            current = lang
        resolved.append(current)
    return resolved


def group_by_language(
    intervals: list[Interval],
    languages: list[str],
    *,
    max_sec: float = 30.0,
) -> list[tuple[str, list[Interval]]]:
    """Group consecutive same-language intervals, capping total speech at max_sec."""
    groups: list[tuple[str, list[Interval]]] = []
    total = 0.0
    for interval, lang in zip(intervals, languages):
        dur = interval[1] - interval[0]
        if groups and groups[-1][0] == lang and total + dur <= max_sec:
            groups[-1][1].append(interval)
            total += dur
        else:
            groups.append((lang, [interval]))
            total = dur
    return groups


def slice_intervals(
    audio: np.ndarray, intervals: list[Interval], sample_rate: int = 16_000
) -> np.ndarray:
    chunks = [audio[round(s * sample_rate) : round(e * sample_rate)] for s, e in intervals]
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
