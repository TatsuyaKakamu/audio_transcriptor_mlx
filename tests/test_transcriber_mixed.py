from pathlib import Path

import numpy as np
import pytest

from app.services import transcriber, vad

SR = 16_000


@pytest.fixture
def fake_audio(monkeypatch):
    # en speech at 0-5s, ja speech at 10-15s, 0.5s blip at 20s
    audio = np.zeros(SR * 25, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)
    monkeypatch.setattr(
        vad, "detect_speech_intervals", lambda _a: [(0.0, 5.0), (10.0, 15.0), (20.0, 20.5)]
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
    # short 0.5s blip is not detected and inherits ja, merging into the ja group
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
