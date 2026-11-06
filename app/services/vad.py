import bisect
from pathlib import Path

import numpy as np

_SAMPLE_RATE = 16_000


def _load_audio(source_path: Path) -> np.ndarray:
    # Decode via ffmpeg (same path mlx-whisper uses). The previous soundfile + resampy
    # path silently disabled VAD for any non-16kHz file because resampy is not installed.
    from mlx_whisper.audio import load_audio
    return np.asarray(load_audio(str(source_path), sr=_SAMPLE_RATE), dtype=np.float32)


def _get_vad_model():
    from silero_vad import load_silero_vad  # type: ignore[import]
    return load_silero_vad()


def detect_speech_intervals(
    audio: np.ndarray,
    min_silence_ms: int = 500,
    speech_pad_ms: int = 200,
) -> list[tuple[float, float]]:
    """Return speech intervals (orig_start_sec, orig_end_sec); empty if no speech."""
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
    return [(seg["start"] / _SAMPLE_RATE, seg["end"] / _SAMPLE_RATE) for seg in raw_ts]


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
    kept_intervals = detect_speech_intervals(audio, min_silence_ms, speech_pad_ms)

    if not kept_intervals:
        # No speech detected — return original audio unchanged.
        duration = len(audio) / _SAMPLE_RATE
        return audio, [(0.0, duration)]

    chunks = [
        audio[round(start * _SAMPLE_RATE) : round(end * _SAMPLE_RATE)]
        for start, end in kept_intervals
    ]
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
