import logging
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
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}

MIXED_LANGUAGE = "mixed"
_MAX_DETECT_FAILURES = 3

_DECODE_OPTIONS = {
    "temperature": 0.0,
    "condition_on_previous_text": False,
    "no_speech_threshold": 0.6,
}


logger = logging.getLogger(__name__)

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

    if language == MIXED_LANGUAGE:
        segments = _transcribe_mixed(source_path, model, repo, progress_callback, use_vad)
    else:
        segments = _transcribe_single(source_path, repo, language, progress_callback, use_vad)

    from app.services.segment_merger import merge_by_conversation
    segments = merge_by_conversation(segments, language=language)
    return TranscriptionResult(
        source_path=source_path,
        language=language,
        model=model,
        segments=segments,
    )


def _transcribe_single(
    source_path: Path,
    repo: str,
    language: str,
    progress_callback: Callable[[int, int, float], None] | None,
    use_vad: bool,
) -> list[Segment]:
    audio_input: str | object = str(source_path)
    kept_intervals = None

    if use_vad:
        try:
            from app.services.vad import preprocess_with_vad
            audio_input, kept_intervals = preprocess_with_vad(source_path)
        except Exception:
            logger.warning("VAD failed for %s; transcribing without VAD", source_path, exc_info=True)
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
            **_DECODE_OPTIONS,
        )
    finally:
        if progress_callback is not None:
            _tqdm_module.tqdm = original_tqdm_cls

    return normalize_segments(result, kept_intervals)


class NotMultilingualModelError(ValueError):
    pass


def _detect_ja_en(pcm, repo: str) -> dict[str, float]:
    # Relies on mlx-whisper internals (0.4.x); mirrors what mlx_whisper.transcribe does
    # for language=None, but lets us restrict the choice to ja/en per speech interval.
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    from app.services.mixed_language import CANDIDATE_LANGUAGES

    # float16 matches mlx_whisper.transcribe's default; ModelHolder caches by path only.
    whisper_model = ModelHolder.get_model(repo, mx.float16)
    if not whisper_model.is_multilingual:
        raise NotMultilingualModelError(f"mixed language mode needs a multilingual model: {repo}")
    # Pad with silent audio before the mel (not zeros in log-mel space) as transcribe does;
    # short intervals would otherwise be dominated by an out-of-distribution constant.
    mel = log_mel_spectrogram(pcm, n_mels=whisper_model.dims.n_mels, padding=N_SAMPLES)
    mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
    _, probs = whisper_model.detect_language(mel)
    return {lang: float(probs.get(lang, 0.0)) for lang in CANDIDATE_LANGUAGES}


def _transcribe_mixed(
    source_path: Path,
    model: str,
    repo: str,
    progress_callback: Callable[[int, int, float], None] | None,
    use_vad: bool,
) -> list[Segment]:
    from app.services import mixed_language as ml
    from app.services import vad

    if model.endswith(".en"):
        raise NotMultilingualModelError(f"mixed language mode needs a multilingual model, got {model!r}")

    audio = vad._load_audio(source_path)
    sample_rate = vad._SAMPLE_RATE
    intervals: list[tuple[float, float]] = []
    if use_vad:
        try:
            intervals = vad.detect_speech_intervals(audio)
        except Exception:
            logger.warning("VAD failed for %s; using fixed windows", source_path, exc_info=True)
    if not intervals:
        intervals = [(0.0, len(audio) / sample_rate)]
    intervals = ml.split_long_intervals(audio, intervals, sample_rate=sample_rate)

    durations = [end - start for start, end in intervals]
    probs: list[dict[str, float] | None] = []
    consecutive_failures = 0
    detection_disabled = False
    for interval, duration in zip(intervals, durations):
        if detection_disabled or duration < ml.MIN_DETECT_SEC:
            probs.append(None)
            continue
        try:
            probs.append(_detect_ja_en(ml.slice_intervals(audio, [interval], sample_rate), repo))
            consecutive_failures = 0
        except NotMultilingualModelError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            logger.warning("language detection failed at %.1fs", interval[0],
                           exc_info=consecutive_failures == 1)
            probs.append(None)
            # A missing model or changed internals fail identically for every interval;
            # stop retrying (each retry may re-attempt a model download).
            if isinstance(exc, (ImportError, OSError)) or consecutive_failures >= _MAX_DETECT_FAILURES:
                detection_disabled = True
                logger.warning("language detection disabled; unresolved intervals use %s",
                               ml.DEFAULT_LANGUAGE)

    languages = ml.resolve_languages(durations, probs)
    groups = ml.group_by_language(intervals, languages)
    logger.info("mixed mode: %d intervals -> %d groups (%s)", len(intervals), len(groups),
                ", ".join(f"{lang}:{len(ivs)}" for lang, ivs in groups))

    # tqdm in mlx_whisper counts mel frames (one per 160 samples).
    group_frames = [
        sum(round(e * sample_rate) - round(s * sample_rate) for s, e in ivs) // 160
        for _, ivs in groups
    ]
    total_frames = max(1, sum(group_frames))
    offset = 0

    def on_group_progress(n: int, _total: int, elapsed: float) -> None:
        progress_callback(min(offset + n, total_frames), total_frames, elapsed)

    original_tqdm_cls = _tqdm_module.tqdm
    start = time.monotonic()
    if progress_callback is not None:
        _tqdm_module.tqdm = _make_progress_tqdm(on_group_progress, start)

    segments: list[Segment] = []
    try:
        for (lang, ivs), frames in zip(groups, group_frames):
            result = mlx_whisper.transcribe(
                ml.slice_intervals(audio, ivs, sample_rate),
                path_or_hf_repo=repo,
                language=lang,
                **_DECODE_OPTIONS,
            )
            segments.extend(normalize_segments(result, ivs))
            # Windows skipped as silence never reach pbar.update, so advance by the
            # group's full length rather than the last reported n.
            offset += frames
    finally:
        if progress_callback is not None:
            _tqdm_module.tqdm = original_tqdm_cls

    if progress_callback is not None:
        progress_callback(total_frames, total_frames, time.monotonic() - start)

    return segments


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
