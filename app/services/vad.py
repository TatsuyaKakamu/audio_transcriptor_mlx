import bisect
from pathlib import Path

import numpy as np
import soundfile as sf

_SAMPLE_RATE = 16_000


def _load_audio(source_path: Path) -> np.ndarray:
    data, sr = sf.read(str(source_path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != _SAMPLE_RATE:
        import resampy  # optional fast resample; falls back to soundfile's built-in if absent
        mono = resampy.resample(mono, sr, _SAMPLE_RATE)
    return mono.astype(np.float32)


def _get_vad_model():
    from silero_vad import load_silero_vad  # type: ignore[import]
    return load_silero_vad()


def preprocess_with_vad(
    source_path: Path,
    min_silence_ms: int = 500,
    speech_pad_ms: int = 200,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Return (concatenated_speech_pcm, kept_intervals).

    kept_intervals is a list of (orig_start_sec, orig_end_sec) for each
    retained speech chunk, in order.  Use remap_timestamp() to convert a
    VAD-timeline timestamp back to the original timeline.
    """
    audio = _load_audio(source_path)
    model = _get_vad_model()

    from silero_vad import get_speech_timestamps  # type: ignore[import]

    raw_ts = get_speech_timestamps(
        audio,
        model,
        sampling_rate=_SAMPLE_RATE,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
        return_seconds=False,
    )

    if not raw_ts:
        # No speech detected — return original audio unchanged.
        duration = len(audio) / _SAMPLE_RATE
        return audio, [(0.0, duration)]

    chunks = []
    kept_intervals: list[tuple[float, float]] = []
    for seg in raw_ts:
        start_idx, end_idx = seg["start"], seg["end"]
        chunks.append(audio[start_idx:end_idx])
        kept_intervals.append((start_idx / _SAMPLE_RATE, end_idx / _SAMPLE_RATE))

    concatenated = np.concatenate(chunks)
    return concatenated, kept_intervals


def remap_timestamp(t: float, kept_intervals: list[tuple[float, float]]) -> float:
    """Map a timestamp in the VAD-concatenated timeline back to the original timeline.

    VAD-timeline t=0 corresponds to the start of the first kept interval.
    Segments are laid back-to-back without gaps.
    """
    # cumulative durations after each interval (VAD timeline boundaries)
    cumulative = 0.0
    boundaries: list[float] = []
    for orig_start, orig_end in kept_intervals:
        cumulative += orig_end - orig_start
        boundaries.append(cumulative)

    if t >= boundaries[-1]:
        orig_start, orig_end = kept_intervals[-1]
        return orig_end

    idx = bisect.bisect_right(boundaries, t)
    if idx >= len(kept_intervals):
        idx = len(kept_intervals) - 1

    # how far into this interval are we?
    interval_start_vad = boundaries[idx - 1] if idx > 0 else 0.0
    offset_into_interval = t - interval_start_vad
    orig_start, _ = kept_intervals[idx]
    return orig_start + offset_into_interval
