from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import MinutesConfig
from app.services import minutes
from app.services.minutes_generator import GeneratedMinutes, MinutesGenerationError


def _setup_paths(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"x")
    target = _dt.datetime(2026, 5, 8, 10, 0, 0).timestamp()
    os.utime(audio, (target, target))
    transcript = tmp_path / "meeting.transcript.md"
    transcript.write_text("...", encoding="utf-8")
    return audio, transcript


def _enabled_cfg(**overrides) -> MinutesConfig:
    base = dict(enabled=True, model="gemma4", request_timeout_seconds=10.0)
    base.update(overrides)
    return MinutesConfig(**base)


def test_run_for_disabled_returns_none_and_skips_generator(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    gen_mock = MagicMock()
    monkeypatch.setattr(minutes.minutes_generator, "generate_minutes", gen_mock)

    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=MinutesConfig(enabled=False),
    )
    assert out is None
    gen_mock.assert_not_called()


def test_run_for_writes_file_on_success(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(return_value=GeneratedMinutes(topic="予算会議", body_markdown="# 予算会議\n## 概要\n要約")),
    )

    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
    )
    assert out is not None
    assert out.output_path == tmp_path / "2026-05-08_予算会議.md"
    assert out.topic_sanitized == "予算会議"
    content = out.output_path.read_text(encoding="utf-8")
    assert "topic: 予算会議" in content
    assert "ollama_model: gemma4" in content
    assert "# 予算会議" in content


def test_run_for_logs_and_notifies_on_success(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(return_value=GeneratedMinutes(topic="t", body_markdown="# t")),
    )
    log_mock = MagicMock()
    notify_mock = MagicMock()

    minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
        on_log=log_mock,
        notify=notify_mock,
    )
    assert any("議事録生成中" in c.args[1] for c in log_mock.call_args_list)
    assert any("Saved minutes" in c.args[1] for c in log_mock.call_args_list)
    assert any(c.args[0] == "議事録生成中…" for c in notify_mock.call_args_list)
    assert any(c.args[0] == "議事録生成完了" for c in notify_mock.call_args_list)


def test_run_for_swallows_generation_failure(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(side_effect=MinutesGenerationError("boom")),
    )
    log_mock = MagicMock()
    notify_mock = MagicMock()

    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
        on_log=log_mock,
        notify=notify_mock,
    )
    assert out is None
    assert any("議事録生成失敗" in c.args[1] for c in log_mock.call_args_list)
    assert any(c.args[0] == "議事録生成失敗" for c in notify_mock.call_args_list)
    # トランスクリプトは無傷
    assert transcript.read_text(encoding="utf-8") == "..."
    # 議事録ファイルは作られない
    assert not list(tmp_path.glob("2026-*.md"))


def test_run_for_swallows_unexpected_exception(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(side_effect=ValueError("unexpected")),
    )
    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
    )
    assert out is None


def test_run_for_collision_appends_suffix(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    (tmp_path / "2026-05-08_予算会議.md").write_text("既存", encoding="utf-8")
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(return_value=GeneratedMinutes(topic="予算会議", body_markdown="# 予算会議")),
    )
    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
    )
    assert out is not None
    assert out.output_path == tmp_path / "2026-05-08_予算会議.1.md"
    # 既存ファイルは触らない
    assert (tmp_path / "2026-05-08_予算会議.md").read_text(encoding="utf-8") == "既存"


def test_run_for_callback_exceptions_are_swallowed(tmp_path, monkeypatch) -> None:
    audio, transcript = _setup_paths(tmp_path)
    monkeypatch.setattr(
        minutes.minutes_generator,
        "generate_minutes",
        MagicMock(return_value=GeneratedMinutes(topic="x", body_markdown="# x")),
    )

    def angry_log(level, msg):
        raise RuntimeError("log boom")

    def angry_notify(title, body):
        raise RuntimeError("notify boom")

    out = minutes.run_for(
        transcript_path=transcript,
        audio_path=audio,
        transcript_text="text",
        language="ja",
        whisper_model="medium",
        cfg=_enabled_cfg(),
        on_log=angry_log,
        notify=angry_notify,
    )
    assert out is not None  # ファイルは依然として書かれる
