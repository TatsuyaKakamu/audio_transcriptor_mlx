from __future__ import annotations

import json
import logging
import urllib.error
from pathlib import Path

import pytest

from http.client import HTTPException

from app.config import MinutesConfig
from app.models.types import Segment, TranscriptionResult
from app.services import minutes_generator
from app.services.minutes_generator import (
    GeneratedMinutes,
    MinutesGenerationError,
    _strip_json_fence,
    generate_minutes,
    transcript_plain_text,
)


def _cfg(**overrides) -> MinutesConfig:
    base = dict(
        enabled=True,
        ollama_host="http://localhost:11434",
        model="gemma4",
        prompt_language="ja",
        max_input_chars=60000,
        request_timeout_seconds=10.0,
    )
    base.update(overrides)
    return MinutesConfig(**base)


@pytest.fixture(autouse=True)
def _block_real_http(monkeypatch):
    """Safety net: any test that doesn't explicitly stub _http_post_json fails fast."""

    def _explode(*args, **kwargs):
        raise AssertionError("real _http_post_json call not allowed in tests")

    monkeypatch.setattr(minutes_generator, "_http_post_json", _explode)


def test_transcript_plain_text_strips_timestamps_and_skips_empty() -> None:
    result = TranscriptionResult(
        source_path=Path("/x.wav"),
        language="ja",
        model="medium",
        segments=[
            Segment(0.0, 1.0, "おはようございます。"),
            Segment(1.0, 2.0, ""),
            Segment(2.0, 3.0, "本日の議題は予算です。"),
        ],
    )
    assert transcript_plain_text(result) == "おはようございます。\n本日の議題は予算です。"


def test_generate_minutes_calls_ollama_with_correct_payload(monkeypatch) -> None:
    captured = {}

    def fake_post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"response": json.dumps({"topic": "予算", "minutes_markdown": "# 予算\n本文"})}

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)

    cfg = _cfg(ollama_host="http://example.local:1234")
    out = generate_minutes("transcript-text", language="ja", cfg=cfg)

    assert captured["url"] == "http://example.local:1234/api/generate"
    assert captured["payload"]["model"] == "gemma4"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["format"] == "json"
    assert "transcript-text" in captured["payload"]["prompt"]
    assert captured["payload"]["options"]["num_ctx"] == 32768
    assert captured["timeout"] == 10.0
    assert out == GeneratedMinutes(topic="予算", body_markdown="# 予算\n本文")


def test_generate_minutes_truncates_long_transcript(monkeypatch, caplog) -> None:
    captured = {}

    def fake_post(url, payload, timeout):
        captured["payload"] = payload
        return {"response": json.dumps({"topic": "x", "minutes_markdown": "# x"})}

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)

    cfg = _cfg(max_input_chars=100)
    long_text = "あ" * 500
    with caplog.at_level(logging.WARNING):
        generate_minutes(long_text, language="ja", cfg=cfg)
    assert "あ" * 100 in captured["payload"]["prompt"]
    assert "あ" * 101 not in captured["payload"]["prompt"]
    assert any("truncating" in rec.message for rec in caplog.records)


def test_generate_minutes_invalid_inner_json_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        minutes_generator,
        "_http_post_json",
        lambda *a, **k: {"response": "not json at all"},
    )
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


def test_generate_minutes_missing_topic_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        minutes_generator,
        "_http_post_json",
        lambda *a, **k: {"response": json.dumps({"minutes_markdown": "# x"})},
    )
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


def test_generate_minutes_missing_body_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        minutes_generator,
        "_http_post_json",
        lambda *a, **k: {"response": json.dumps({"topic": "x"})},
    )
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


def test_generate_minutes_empty_response_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        minutes_generator,
        "_http_post_json",
        lambda *a, **k: {"response": ""},
    )
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


def test_generate_minutes_http_error_raises(monkeypatch) -> None:
    def fake_post(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


def test_generate_minutes_uses_english_prompts(monkeypatch) -> None:
    captured = {}

    def fake_post(url, payload, timeout):
        captured["payload"] = payload
        return {"response": json.dumps({"topic": "Budget", "minutes_markdown": "# Budget"})}

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)
    generate_minutes("hello world", language="en", cfg=_cfg(prompt_language="en"))
    assert "TRANSCRIPT BEGIN" in captured["payload"]["prompt"]
    assert "meeting secretary" in captured["payload"]["system"]


def test_generate_minutes_num_ctx_zero_omits_from_options(monkeypatch) -> None:
    captured = {}

    def fake_post(url, payload, timeout):
        captured["payload"] = payload
        return {"response": json.dumps({"topic": "x", "minutes_markdown": "# x"})}

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)
    generate_minutes("text", language="ja", cfg=_cfg(num_ctx=0))
    assert "num_ctx" not in captured["payload"]["options"]


def test_generate_minutes_json_fence_is_tolerated(monkeypatch) -> None:
    fenced = '```json\n{"topic": "フェンス", "minutes_markdown": "# フェンス"}\n```'
    monkeypatch.setattr(
        minutes_generator,
        "_http_post_json",
        lambda *a, **k: {"response": fenced},
    )
    out = generate_minutes("x", language="ja", cfg=_cfg())
    assert out.topic == "フェンス"


def test_generate_minutes_http_exception_raises(monkeypatch) -> None:
    def fake_post(*a, **k):
        raise HTTPException("remote disconnected")

    monkeypatch.setattr(minutes_generator, "_http_post_json", fake_post)
    with pytest.raises(MinutesGenerationError):
        generate_minutes("x", language="ja", cfg=_cfg())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('```json\n{"a":1}\n```', '{"a":1}'),
        ('```\n{"a":1}\n```', '{"a":1}'),
        ('  {"a":1}  ', '{"a":1}'),
        ('{"a":1}', '{"a":1}'),
    ],
)
def test_strip_json_fence(raw: str, expected: str) -> None:
    assert _strip_json_fence(raw) == expected
