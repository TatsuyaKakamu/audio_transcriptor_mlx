from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import cli
from app.config import AppConfig, MinutesConfig
from app.models.types import TranscriptionResult


def _cfg(tmp_path: Path, **overrides) -> AppConfig:
    base = AppConfig(
        watch_dir=tmp_path,
        extensions=frozenset({".wav", ".mp3"}),
        file_stability_seconds=0.0,
    )
    return replace(base, **overrides) if overrides else base


def _fake_transcribe(path: Path, model: str, language: str, **_kwargs) -> TranscriptionResult:
    return TranscriptionResult(
        source_path=path,
        language=language,
        model=model,
        segments=[],
    )


@pytest.fixture
def patched(monkeypatch):
    transcribe_mock = MagicMock(side_effect=_fake_transcribe)
    trash_mock = MagicMock()
    monkeypatch.setattr("app.cli.transcriber.transcribe", transcribe_mock)
    monkeypatch.setattr("app.cli.send2trash.send2trash", trash_mock)
    return transcribe_mock, trash_mock


def test_processes_pending_file_and_trashes_source(tmp_path: Path, patched) -> None:
    transcribe_mock, trash_mock = patched
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake wav")

    rc = cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    assert rc == 0
    transcribe_mock.assert_called_once()
    assert (tmp_path / "meeting.transcript.md").exists()
    trash_mock.assert_called_once_with(str(audio))


def test_skips_when_transcript_already_exists(tmp_path: Path, patched) -> None:
    transcribe_mock, trash_mock = patched
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake wav")
    (tmp_path / "meeting.transcript.md").write_text("existing", encoding="utf-8")

    cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    transcribe_mock.assert_not_called()
    trash_mock.assert_not_called()


def test_skips_when_numbered_transcript_exists(tmp_path: Path, patched) -> None:
    transcribe_mock, trash_mock = patched
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake wav")
    (tmp_path / "meeting.transcript.2.md").write_text("existing", encoding="utf-8")

    cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    transcribe_mock.assert_not_called()
    trash_mock.assert_not_called()


def test_ignores_unsupported_extensions(tmp_path: Path, patched) -> None:
    transcribe_mock, _ = patched
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "clip.mov").write_bytes(b"fake")

    cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    transcribe_mock.assert_not_called()


def test_processes_multiple_pending_files(tmp_path: Path, patched) -> None:
    transcribe_mock, trash_mock = patched
    (tmp_path / "a.wav").write_bytes(b"fake")
    (tmp_path / "b.mp3").write_bytes(b"fake")

    cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    assert transcribe_mock.call_count == 2
    assert trash_mock.call_count == 2


def test_skip_trash_when_disabled(tmp_path: Path, patched) -> None:
    _, trash_mock = patched
    (tmp_path / "meeting.wav").write_bytes(b"fake")

    cli.cmd_scan(
        cfg=_cfg(tmp_path, trash_source_after_success=False),
        lock_path=tmp_path / "scan.lock",
    )

    trash_mock.assert_not_called()
    assert (tmp_path / "meeting.wav").exists()


def test_transcribe_failure_keeps_source(tmp_path: Path, monkeypatch) -> None:
    trash_mock = MagicMock()
    monkeypatch.setattr("app.cli.send2trash.send2trash", trash_mock)
    monkeypatch.setattr(
        "app.cli.transcriber.transcribe",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")

    rc = cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    assert rc == 1
    trash_mock.assert_not_called()
    assert audio.exists()
    assert not (tmp_path / "meeting.transcript.md").exists()


def test_flock_skips_second_instance(tmp_path: Path, patched) -> None:
    import fcntl

    transcribe_mock, _ = patched
    (tmp_path / "meeting.wav").write_bytes(b"fake")

    lock_path = tmp_path / "scan.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=lock_path)

    assert rc == 0
    transcribe_mock.assert_not_called()


def test_missing_watch_dir_is_not_error(tmp_path: Path, patched) -> None:
    transcribe_mock, _ = patched
    missing = tmp_path / "does-not-exist"
    rc = cli.cmd_scan(
        cfg=_cfg(tmp_path, watch_dir=missing),
        lock_path=tmp_path / "scan.lock",
    )
    assert rc == 0
    transcribe_mock.assert_not_called()


def test_minutes_skipped_when_disabled(tmp_path: Path, patched, monkeypatch) -> None:
    minutes_mock = MagicMock()
    monkeypatch.setattr("app.cli.minutes.run_for", minutes_mock)
    (tmp_path / "meeting.wav").write_bytes(b"fake")

    cli.cmd_scan(cfg=_cfg(tmp_path), lock_path=tmp_path / "scan.lock")

    minutes_mock.assert_not_called()


def test_minutes_invoked_when_enabled(tmp_path: Path, patched, monkeypatch) -> None:
    minutes_mock = MagicMock(return_value=None)
    monkeypatch.setattr("app.cli.minutes.run_for", minutes_mock)
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")

    cfg = _cfg(tmp_path, minutes=MinutesConfig(enabled=True))
    cli.cmd_scan(cfg=cfg, lock_path=tmp_path / "scan.lock")

    minutes_mock.assert_called_once()
    kwargs = minutes_mock.call_args.kwargs
    assert kwargs["audio_path"] == audio
    assert kwargs["transcript_path"] == tmp_path / "meeting.transcript.md"
    assert kwargs["language"] == "ja"
    assert kwargs["whisper_model"] == "medium"
    assert kwargs["cfg"].enabled is True
    # 通知コールバックは notifier.notify が渡される
    assert kwargs["notify"] is not None


def test_minutes_none_return_does_not_block_trash(tmp_path: Path, patched, monkeypatch) -> None:
    _, trash_mock = patched
    monkeypatch.setattr("app.cli.minutes.run_for", MagicMock(return_value=None))
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")

    cfg = _cfg(tmp_path, minutes=MinutesConfig(enabled=True))
    rc = cli.cmd_scan(cfg=cfg, lock_path=tmp_path / "scan.lock")

    assert rc == 0
    trash_mock.assert_called_once_with(str(audio))
