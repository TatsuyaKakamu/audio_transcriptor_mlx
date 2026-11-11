import functools
from pathlib import Path

import numpy as np
import pytest

from app.config import MixedConfig, VadConfig
from app.services import transcriber, vad

SR = 16_000


@pytest.fixture(autouse=True)
def multilingual_model(monkeypatch):
    monkeypatch.setattr(transcriber, "_check_multilingual", lambda _repo: None)


@pytest.fixture(autouse=True)
def vad_enabled(monkeypatch):
    # VAD is off by default; most tests here drive the VAD interval path, so they
    # get vad=VadConfig(enabled=True) unless they pass vad= themselves.
    monkeypatch.setattr(
        transcriber, "transcribe", functools.partial(transcriber.transcribe, vad=VadConfig(enabled=True))
    )


@pytest.fixture
def fake_audio(monkeypatch):
    # en speech at 0-5s, ja speech at 10-15s, 0.3s blip at 20s
    audio = np.zeros(SR * 25, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)
    monkeypatch.setattr(
        vad, "detect_speech_intervals", lambda _a, **_kw: [(0.0, 5.0), (10.0, 15.0), (20.0, 20.3)]
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

    def vad_boom(_a, **_kw):
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
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(i * 6.0, i * 6.0 + 5.0) for i in range(10)])
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


def test_mixed_stops_on_english_only_model_before_detection(monkeypatch, fake_audio):
    def english_only(_repo):
        raise transcriber.NotMultilingualModelError("english only")

    monkeypatch.setattr(transcriber, "_check_multilingual", english_only)
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: pytest.fail("should not detect"))
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", lambda _p, **_k: pytest.fail("should not transcribe"), raising=False)
    with pytest.raises(transcriber.NotMultilingualModelError):
        transcriber.transcribe(Path("x.wav"), "my-english-only", "mixed")


def test_mixed_model_load_failure_disables_detection(monkeypatch, fake_audio):
    def offline(_repo):
        raise OSError("offline")

    monkeypatch.setattr(transcriber, "_check_multilingual", offline)
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: pytest.fail("should not detect"))
    seen = []
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe",
                        lambda _p, *, language, **_k: seen.append(language) or {"segments": []}, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert seen == ["ja"]


def test_mixed_failure_count_resets_after_success(monkeypatch):
    audio = np.zeros(SR * 60, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(i * 6.0, i * 6.0 + 5.0) for i in range(6)])
    outcomes = iter(["fail", "fail", "en", "fail", "fail", "ja"])
    calls = []

    def detect(_pcm, _repo):
        outcome = next(outcomes)
        calls.append(outcome)
        if outcome == "fail":
            raise RuntimeError("flaky")
        return {"ja": 0.9, "en": 0.0} if outcome == "ja" else {"ja": 0.0, "en": 0.9}

    seen = []
    monkeypatch.setattr(transcriber, "_detect_ja_en", detect)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe",
                        lambda _p, *, language, **_k: seen.append(language) or {"segments": []}, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert len(calls) == 6
    # leading failures take the first reliable language (en); the last interval is ja
    assert seen == ["en", "ja"]


def test_mixed_without_vad_covers_whole_file(monkeypatch):
    audio = np.zeros(SR * 70, dtype=np.float32)
    monkeypatch.setattr(vad, "_load_audio", lambda _p: audio)

    def must_not_run(*_a, **_kw):
        raise AssertionError("VAD must not run when disabled")

    monkeypatch.setattr(vad, "detect_speech_intervals", must_not_run)
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.9, "en": 0.0})
    lengths = []

    def fake_transcribe(pcm, **_kw):
        lengths.append(len(pcm))
        return {"segments": []}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed", vad=VadConfig(enabled=False))
    assert sum(lengths) == len(audio)


def test_mixed_passes_vad_options(monkeypatch, fake_audio):
    seen = {}

    def fake_detect(_a, **kw):
        seen.update(kw)
        return [(0.0, 5.0)]

    monkeypatch.setattr(vad, "detect_speech_intervals", fake_detect)
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.9, "en": 0.0})
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", lambda pcm, **_kw: {"segments": []},
                        raising=False)
    cfg = VadConfig(enabled=True, threshold=0.3, min_silence_ms=800, speech_pad_ms=400)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed", vad=cfg)
    assert seen == {"threshold": 0.3, "min_silence_ms": 800, "speech_pad_ms": 400}


def _en_then_retry(monkeypatch, en_text, ja_text):
    monkeypatch.setattr(vad, "_load_audio", lambda _p: np.zeros(SR * 10, dtype=np.float32))
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(0.0, 10.0)])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.0, "en": 0.9})
    calls = []

    def fake_transcribe(pcm, *, language, **_kw):
        calls.append(language)
        text = en_text if language == "en" else ja_text
        return {"segments": [{"start": 0.0, "end": 10.0, "text": text}]}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    return calls, [s.text for s in result.segments]


def test_sparse_en_group_is_retried_as_ja(monkeypatch):
    calls, texts = _en_then_retry(monkeypatch, "Yeah.", "前提としてのフローということもあって対処しました")
    assert calls == ["en", "ja"]
    assert texts == ["前提としてのフローということもあって対処しました"]


def test_dense_en_group_is_kept(monkeypatch):
    en = "We should review the vulnerability handling flow before the next release."
    calls, texts = _en_then_retry(monkeypatch, en, "はい")
    assert calls == ["en"]
    assert texts == [en]


def test_sparse_retry_keeps_en_when_ja_is_not_longer(monkeypatch):
    calls, texts = _en_then_retry(monkeypatch, "Yeah.", "")
    assert calls == ["en", "ja"]
    assert texts == ["Yeah."]


def test_mixed_uses_en_min_margin(monkeypatch, fake_audio):
    # en wins by a 0.3 margin: enough at the old 0.2 threshold, not at 0.5
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.35, "en": 0.65})
    langs = []

    def fake_transcribe(pcm, *, language, **_kw):
        langs.append(language)
        return {"segments": []}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert set(langs) == {"ja"}
    langs.clear()
    transcriber.transcribe(Path("x.wav"), "medium", "mixed", mixed=MixedConfig(en_min_margin=0.2))
    assert "en" in langs


def test_retry_suppresses_progress_and_stays_monotonic(monkeypatch):
    monkeypatch.setattr(vad, "_load_audio", lambda _p: np.zeros(SR * 10, dtype=np.float32))
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(0.0, 10.0)])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.0, "en": 0.9})

    def fake(pcm, *, language, **_kw):
        import tqdm

        total = len(pcm) // 160
        with tqdm.tqdm(total=total) as pbar:
            pbar.update(total if language == "en" else 1)
        text = "Yeah." if language == "en" else "日本語の長い発言がここにあります"
        return {"segments": [{"start": 0.0, "end": 10.0, "text": text}]}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake, raising=False)
    progress = []
    transcriber.transcribe(Path("x.wav"), "medium", "mixed",
                           progress_callback=lambda n, t, e: progress.append(n))
    assert progress == sorted(progress)
    assert 1 not in progress  # the ja retry's update was not forwarded


def test_short_sparse_en_group_is_not_retried(monkeypatch):
    monkeypatch.setattr(vad, "_load_audio", lambda _p: np.zeros(SR * 10, dtype=np.float32))
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(0.0, 2.0)])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.0, "en": 0.9})
    calls = []

    def fake(pcm, *, language, **_kw):
        calls.append(language)
        return {"segments": [{"start": 0.0, "end": 2.0, "text": "OK"}]}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert calls == ["en"]


def test_retry_failure_keeps_en_result(monkeypatch):
    monkeypatch.setattr(vad, "_load_audio", lambda _p: np.zeros(SR * 10, dtype=np.float32))
    monkeypatch.setattr(vad, "detect_speech_intervals", lambda _a, **_kw: [(0.0, 10.0)])
    monkeypatch.setattr(transcriber, "_detect_ja_en", lambda _p, _r: {"ja": 0.0, "en": 0.9})

    def fake(pcm, *, language, **_kw):
        if language == "ja":
            raise RuntimeError("boom")
        return {"segments": [{"start": 0.0, "end": 10.0, "text": "Yeah."}]}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "mixed")
    assert [s.text for s in result.segments] == ["Yeah."]


def test_single_language_skips_vad_when_disabled(monkeypatch):
    def must_not_run(*_a, **_kw):
        raise AssertionError("VAD must not run when disabled")

    monkeypatch.setattr(vad, "preprocess_with_vad", must_not_run)
    seen = []
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe",
                        lambda audio, **_kw: seen.append(audio) or {"segments": []}, raising=False)
    transcriber.transcribe(Path("x.wav"), "medium", "ja", vad=VadConfig(enabled=False))
    assert seen == ["x.wav"]


def test_single_language_passes_vad_options(monkeypatch):
    seen = {}

    def fake_pre(_path, **kw):
        seen.update(kw)
        return np.zeros(SR, dtype=np.float32), [(0.0, 1.0)]

    monkeypatch.setattr(vad, "preprocess_with_vad", fake_pre)
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", lambda a, **_kw: {"segments": []},
                        raising=False)
    cfg = VadConfig(enabled=True, threshold=0.3, min_silence_ms=800, speech_pad_ms=400)
    transcriber.transcribe(Path("x.wav"), "medium", "ja", vad=cfg)
    assert seen == {"threshold": 0.3, "min_silence_ms": 800, "speech_pad_ms": 400}


def test_transcribe_collapses_repeated_phrases_and_enables_fallback(monkeypatch):
    seen = {}

    def fake_transcribe(_audio, **kw):
        seen.update(kw)
        return {"segments": [{"start": 0.0, "end": 5.0, "text": "冒頭" + "お客様の" * 8 + "。"}]}

    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe", fake_transcribe, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "ja", vad=VadConfig(enabled=False))
    assert [s.text for s in result.segments] == ["冒頭お客様の[反復省略]。"]
    assert isinstance(seen["temperature"], tuple) and len(seen["temperature"]) > 1
    assert seen["compression_ratio_threshold"] == 2.4


def test_identical_sentence_blocks_are_collapsed_keeping_span(monkeypatch):
    looped = [{"start": i * 2.0, "end": i * 2.0 + 2.0, "text": "ご視聴ありがとうございました。"} for i in range(8)]
    tail = [{"start": 20.0, "end": 21.0, "text": "はい。"}, {"start": 22.0, "end": 23.0, "text": "はい。"}]
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe",
                        lambda _a, **_kw: {"segments": looped + tail}, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "ja", vad=VadConfig(enabled=False))
    assert [(s.start_sec, s.end_sec, s.text) for s in result.segments] == [
        (0.0, 16.0, "ご視聴ありがとうございました。[反復省略]"),
        (20.0, 21.0, "はい。"),
        (22.0, 23.0, "はい。"),
    ]


def test_looping_ja_retry_does_not_win_on_length(monkeypatch):
    en = {"segments": [{"start": 0.0, "end": 10.0, "text": "Okay, let us start the review now."}]}
    ja = {"segments": [{"start": 0.0, "end": 10.0, "text": "お客様の" * 30}]}
    assert transcriber._text_len(ja) < transcriber._text_len(en)


@pytest.mark.parametrize(("repeats", "collapsed"), [(4, False), (5, True)])
def test_identical_block_threshold(monkeypatch, repeats, collapsed):
    segs = [{"start": i * 2.0, "end": i * 2.0 + 1.0, "text": "ありがとうございます。"} for i in range(repeats)]
    monkeypatch.setattr(transcriber.mlx_whisper, "transcribe",
                        lambda _a, **_kw: {"segments": segs}, raising=False)
    result = transcriber.transcribe(Path("x.wav"), "medium", "ja", vad=VadConfig(enabled=False))
    assert (len(result.segments) == 1) == collapsed
