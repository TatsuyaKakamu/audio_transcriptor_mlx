from pathlib import Path

import numpy as np
import pytest

from app.services import transcriber, vad

SR = 16_000


@pytest.fixture
def fake_audio(monkeypatch):
    # en speech at 0-5s, ja speech at 10-15s, 0.3s blip at 20s
    audio = np.zeros(SR * 25, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)
    monkeypatch.setattr(
        vad, "detect_speech_intervals", lambda _a: [(0.0, 5.0), (10.0, 15.0), (20.0, 20.3)]
    )
    return audio


def test_mixed_transcribes_each_language_group(monkeypatch, fake_audio):
    calls = []

    def detect(pcm, repo):
        calls.append(len(pcm))
        return {"ja": 0.05, "en": 0.9} if len(calls) == 1 else {"ja": 0.9, "en": 0.05}

    def fake_transcribe(pcm, *, language, **_kw):
        return {"segments": [{"start": 0.5, "end": 1.0, "text": f"{language} text"}]}

    monkeypatch.setattr(transcriber, "_detect_ja_en", detect)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)

    progress = []
    result = transcriber.transcribe(
        Path("x.wav"), "large-v3-turbo", "mixed", progress_callback=lambda n, t, e: progress.append((n, t))
    )
    # short 0.3s blip is not detected and inherits ja, merging into the ja group
    assert calls == [5 * SR, 5 * SR]
    assert result.language == "mixed"
    texts = [(round(s.start_sec, 1), s.text) for s in result.segments]
    assert texts == [(0.5, "en text"), (10.5, "ja text")]
    assert progress[-1][0] == progress[-1][1]


def test_mixed_survives_detection_failure(monkeypatch, fake_audio):
    def boom(_pcm, _repo):
        raise RuntimeError("internal API changed")

    seen = []

    def fake_transcribe(pcm, *, language, **_kw):
        seen.append(language)
        return {"segments": []}

    monkeypatch.setattr(transcriber, "_detect_ja_en", boom)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert seen == ["ja"]


def test_mixed_vad_failure_uses_windows(monkeypatch):
    audio = np.random.default_rng(0).standard_normal(SR * 70).astype(np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)

    def vad_boom(_a):
        raise RuntimeError("vad broken")

    monkeypatch.setattr(vad, "detect_speech_intervals", vad_boom)
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.9, "en": 0.0})
    lengths = []

    def fake_transcribe(pcm, **_kw):
        lengths.append(len(pcm))
        return {"segments": []}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert sum(lengths) == len(audio)
    assert all(n <= 30 * SR for n in lengths)


def test_mixed_rejects_english_only_model(fake_audio):
    with pytest.raises(ValueError):
        transcriber.transcribe(Path("x.wav"), "small.en", "mixed")


def _tqdm_transcribe(frames_seen):
    """Fake transcribe that drives the (patched) tqdm the way mlx_whisper does."""

    def fake(pcm, **_kw):
        import tqdm

        total = len(pcm) // 160
        with tqdm.tqdm(total=total) as pbar:
            # report only part of the work, like windows skipped as silence
            pbar.update(total // 2)
        frames_seen.append(total)
        return {"segments": []}

    return fake


def test_mixed_progress_is_monotonic_across_groups(monkeypatch, fake_audio):
    langs = iter([{"ja": 0.05, "en": 0.9}, {"ja": 0.9, "en": 0.05}])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: next(langs))
    frames = []
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", _tqdm_transcribe(frames), raising=False)
    progress = []
    transcriber.transcribe(Path("x.wav"), "medium", "mixed", progress_callback=lambda n, t, e: progress.append((n, t)))
    total = sum(frames)
    assert all(t == total for _, t in progress)
    ns = [n for n, _ in progress]
    assert ns == sorted(ns)
    # second group's updates start after the first group's full length
    assert frames[0] + frames[1] // 2 in ns
    assert ns[-1] == total


def test_mixed_restores_tqdm_on_error(monkeypatch, fake_audio):
    import tqdm

    original = tqdm.tqdm
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.9, "en": 0.0})

    def boom(_pcm, **_kw):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", boom, raising=False)
    with pytest.raises(RuntimeError):
        transcriber.transcribe(Path("x.wav"), "medium", "mixed", progress_callback=lambda *a: None)
    assert tqdm.tqdm is original


def test_mixed_stops_detecting_after_repeated_failures(monkeypatch):
    audio = np.zeros(SR * 60, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a: [(i * 6.0, i * 6.0 + 5.0) for i in range(10)])
    calls = []

    def boom(_pcm, _repo):
        calls.append(1)
        raise RuntimeError("same failure")

    monkeypatch.setattr(transcriber, "_detect_ja_en", boom)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", lambda _p, **_k: {"segments": []}, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert len(calls) == transcriber._MAX_DETECT_FAILURES


def test_mixed_stops_detecting_on_missing_model(monkeypatch, fake_audio):
    calls = []

    def missing(_pcm, _repo):
        calls.append(1)
        raise OSError("model not found")

    monkeypatch.setattr(transcriber, "_detect_ja_en", missing)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", lambda _p, **_k: {"segments": []}, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert len(calls) == 1


def test_mixed_second_interval_in_group_is_remapped(monkeypatch, fake_audio):
    langs = iter([{"ja": 0.05, "en": 0.9}, {"ja": 0.9, "en": 0.05}])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: next(langs))

    def fake(pcm, *, language, **_kw):
        if language == "ja":  # group [(10, 15), (20, 20.3)]: 5.2s falls in the second interval
            return {"segments": [{"start": 5.2, "end": 5.4, "text": "うん"}]}
        return {"segments": []}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert round(result.segments[0].start_sec, 1) == 20.2


def test_detect_ja_en_pads_audio_like_transcribe(monkeypatch):
    import types

    mlx_core = pytest.importorskip("mlx.core")
    import mlx_whisper.audio as mw_audio
    import mlx_whisper.transcribe  # noqa: F401
    mw_transcribe = __import__("sys").modules["mlx_whisper.transcribe"]

    seen = {}

    class FakeModel:
        is_multilingual = True
        dims = types.SimpleNamespace(n_mels=128)

        def detect_language(self, mel):
            seen["shape"] = tuple(mel.shape)
            seen["dtype"] = mel.dtype
            return None, {"ja": 0.2, "en": 0.7, "hi": 0.1}

    real_mel = mw_audio.log_mel_spectrogram

    def spy_mel(audio, n_mels=80, padding=0):
        seen["padding"] = padding
        seen["n_mels"] = n_mels
        return real_mel(audio, n_mels=n_mels, padding=padding)

    monkeypatch.setattr(mw_audio, "log_mel_spectrogram", spy_mel)
    monkeypatch.setattr(mw_transcribe.ModelHolder, "get_model", classmethod(lambda cls, p, d: FakeModel()))
    probs = transcriber._detect_ja_en(np.zeros(SR * 3, dtype=np.float32), "repo")
    assert probs == {"ja": 0.2, "en": 0.7}
    assert seen["padding"] == mw_audio.N_SAMPLES
    assert seen["n_mels"] == 128
    assert seen["shape"] == (mw_audio.N_FRAMES, 128)
    assert seen["dtype"] == mlx_core.float16


def test_detect_ja_en_rejects_english_only_model(monkeypatch):
    pytest.importorskip("mlx.core")
    import mlx_whisper.transcribe  # noqa: F401
    mw_transcribe = __import__("sys").modules["mlx_whisper.transcribe"]

    class EnglishOnly:
        is_multilingual = False

    monkeypatch.setattr(mw_transcribe.ModelHolder, "get_model", classmethod(lambda cls, p, d: EnglishOnly()))
    with pytest.raises(transcriber.NotMultilingualModelError):
        transcriber._detect_ja_en(np.zeros(SR, dtype=np.float32), "repo")
