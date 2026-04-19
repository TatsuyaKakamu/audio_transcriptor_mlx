import os
import time
from collections.abc import Callable
from pathlib import Path

import mlx_whisper
import tqdm as _tqdm_module

from app.models.types import Segment, TranscriptionResult

_MODEL_REPO_MAP: dict[str, str] = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


_DEVNULL = open(os.devnull, "w")


def _make_progress_tqdm(callback: Callable[[int, int, float], None], start: float):
    base = _tqdm_module.tqdm

    class _ProgressTqdm(base):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = False
            kwargs.setdefault("file", _DEVNULL)
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            super().update(n)
            if self.total:
                callback(int(self.n), int(self.total), time.monotonic() - start)

    return _ProgressTqdm


def transcribe(
    source_path: Path,
    model: str,
    language: str,
    progress_callback: Callable[[int, int, float], None] | None = None,
    use_vad: bool = True,
) -> TranscriptionResult:
    repo = _MODEL_REPO_MAP.get(model, f"mlx-community/whisper-{model}-mlx")

    audio_input: str | object = str(source_path)
    kept_intervals = None

    if use_vad:
        try:
            from app.services.vad import preprocess_with_vad
            audio_input, kept_intervals = preprocess_with_vad(source_path)
        except Exception:
            audio_input = str(source_path)
            kept_intervals = None

    original_tqdm_cls = _tqdm_module.tqdm
    if progress_callback is not None:
        start = time.monotonic()
        _tqdm_module.tqdm = _make_progress_tqdm(progress_callback, start)

    try:
        result = mlx_whisper.transcribe(
            audio_input,
            path_or_hf_repo=repo,
            language=language,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
    finally:
        if progress_callback is not None:
            _tqdm_module.tqdm = original_tqdm_cls

    segments = normalize_segments(result, kept_intervals)
    return TranscriptionResult(
        source_path=source_path,
        language=language,
        model=model,
        segments=segments,
    )


def normalize_segments(
    result: dict,
    kept_intervals: list[tuple[float, float]] | None = None,
) -> list[Segment]:
    from app.services.vad import remap_timestamp

    raw = result.get("segments", [])
    segments: list[Segment] = []
    for seg in raw:
        text = seg.get("text", "").replace("\n", " ").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        if kept_intervals is not None:
            start = remap_timestamp(start, kept_intervals)
            end = remap_timestamp(end, kept_intervals)
        segments.append(Segment(start_sec=start, end_sec=end, text=text))
    segments.sort(key=lambda s: s.start_sec)
    return segments
