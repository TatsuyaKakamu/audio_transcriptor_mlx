from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from app.services.minutes_writer import (
    build_minutes_markdown,
    derive_minutes_filename,
    resolve_minutes_output_path,
    sanitize_slug,
    sanitize_topic,
    write_minutes,
)


def test_sanitize_topic_strips_path_separators_and_specials() -> None:
    assert sanitize_topic("会議/重要:メモ") == "会議重要メモ"


def test_sanitize_topic_replaces_whitespace_with_underscore() -> None:
    assert sanitize_topic("予算   レビュー\t会議") == "予算_レビュー_会議"


def test_sanitize_topic_drops_control_chars() -> None:
    assert sanitize_topic("foo\x00\nbar") == "foo_bar"


def test_sanitize_topic_empty_falls_back() -> None:
    assert sanitize_topic("") == "議題未取得"
    assert sanitize_topic("   ") == "議題未取得"
    assert sanitize_topic("///") == "議題未取得"


def test_sanitize_topic_truncates_to_codepoints() -> None:
    long = "あ" * 200
    out = sanitize_topic(long, max_len=60)
    assert len(out) == 60
    assert all(c == "あ" for c in out)


def test_sanitize_topic_non_string_falls_back() -> None:
    assert sanitize_topic(None) == "議題未取得"  # type: ignore[arg-type]


def test_sanitize_slug_keeps_ascii_and_underscores() -> None:
    assert sanitize_slug("budget review meeting") == "budget_review_meeting"


def test_sanitize_slug_strips_non_ascii() -> None:
    assert sanitize_slug("予算 review") == "review"


def test_sanitize_slug_removes_path_separators_and_specials() -> None:
    assert sanitize_slug("a/b:c*d") == "abcd"


def test_sanitize_slug_empty_or_all_non_ascii_falls_back() -> None:
    assert sanitize_slug("") == "minutes"
    assert sanitize_slug("   ") == "minutes"
    assert sanitize_slug("予算会議") == "minutes"


def test_sanitize_slug_non_string_falls_back() -> None:
    assert sanitize_slug(None) == "minutes"  # type: ignore[arg-type]


def test_sanitize_slug_truncates() -> None:
    out = sanitize_slug("a" * 200, max_len=60)
    assert len(out) == 60


def test_derive_filename_uses_audio_mtime(tmp_path: Path) -> None:
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"x")
    target = _dt.datetime(2025, 7, 4, 12, 0, 0).timestamp()
    os.utime(audio, (target, target))
    assert derive_minutes_filename(audio, "budget_meeting") == "2025-07-04_budget_meeting.md"


def test_derive_filename_forces_english_slug(tmp_path: Path) -> None:
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"x")
    target = _dt.datetime(2025, 7, 4, 12, 0, 0).timestamp()
    os.utime(audio, (target, target))
    assert derive_minutes_filename(audio, "予算会議") == "2025-07-04_minutes.md"


def test_resolve_minutes_collision_appends_suffix(tmp_path: Path) -> None:
    base = "2026-05-08_予算.md"
    first = resolve_minutes_output_path(tmp_path, base)
    assert first == tmp_path / base
    first.write_text("x", encoding="utf-8")
    second = resolve_minutes_output_path(tmp_path, base)
    assert second == tmp_path / "2026-05-08_予算.1.md"
    second.write_text("x", encoding="utf-8")
    third = resolve_minutes_output_path(tmp_path, base)
    assert third == tmp_path / "2026-05-08_予算.2.md"


def test_build_minutes_markdown_frontmatter(tmp_path: Path) -> None:
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"x")
    target = _dt.datetime(2026, 5, 8, 9, 30, 0).timestamp()
    os.utime(audio, (target, target))
    transcript = tmp_path / "meeting.transcript.md"
    transcript.write_text("...", encoding="utf-8")

    content = build_minutes_markdown(
        topic="予算会議",
        body_markdown="# 予算会議\n\n## 概要\n来期予算を確認した。\n",
        audio_path=audio,
        transcript_path=transcript,
        language="ja",
        whisper_model="medium",
        ollama_model="gemma4",
    )
    assert content.startswith("---\n")
    assert "date: 2026-05-08" in content
    assert "source_audio: meeting.wav" in content
    assert "transcript: meeting.transcript.md" in content
    assert "ollama_model: gemma4" in content
    assert "topic: 予算会議" in content
    assert "# 予算会議" in content
    assert "[meeting.transcript.md](meeting.transcript.md)" in content
    assert content.endswith("\n")


def test_write_minutes_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "x.md"
    write_minutes("# こんにちは\n本文\n", out)
    assert out.read_text(encoding="utf-8") == "# こんにちは\n本文\n"
