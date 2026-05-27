from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import MinutesConfig
from app.services import minutes_generator, minutes_writer
from app.services.minutes_generator import MinutesGenerationError

logger = logging.getLogger(__name__)

LogFn = Callable[[str, str], None]
NotifyFn = Callable[[str, str], None]


@dataclass(frozen=True)
class MinutesRunResult:
    output_path: Path
    topic_sanitized: str


def _emit_log(on_log: LogFn | None, level: str, message: str) -> None:
    if on_log is not None:
        try:
            on_log(level, message)
        except Exception:  # noqa: BLE001 — logging callbacks must not crash the worker
            logger.exception("on_log callback raised; suppressed")


def _emit_notify(notify: NotifyFn | None, title: str, body: str) -> None:
    if notify is not None:
        try:
            notify(title, body)
        except Exception:  # noqa: BLE001
            logger.exception("notify callback raised; suppressed")


def run_for(
    *,
    transcript_path: Path,
    audio_path: Path,
    transcript_text: str,
    language: str,
    whisper_model: str,
    cfg: MinutesConfig,
    on_log: LogFn | None = None,
    notify: NotifyFn | None = None,
) -> MinutesRunResult | None:
    if not cfg.enabled:
        return None

    _emit_log(on_log, "INFO", "議事録生成中…")
    _emit_notify(notify, "議事録生成中…", audio_path.name)

    try:
        generated = minutes_generator.generate_minutes(
            transcript_text, language=language, cfg=cfg
        )
    except MinutesGenerationError as e:
        logger.warning("minutes generation failed for %s: %s", audio_path, e)
        _emit_log(on_log, "WARN", f"議事録生成失敗: {e}")
        _emit_notify(notify, "議事録生成失敗", f"{audio_path.name}: {str(e)[:120].replace(chr(10), ' ')}")
        return None
    except Exception as e:  # noqa: BLE001 — never propagate
        logger.exception("unexpected error while generating minutes for %s", audio_path)
        _emit_log(on_log, "ERROR", f"議事録生成失敗(予期せぬエラー): {e}")
        _emit_notify(notify, "議事録生成失敗", f"{audio_path.name}: {str(e)[:120].replace(chr(10), ' ')}")
        return None

    try:
        topic_sanitized = minutes_writer.sanitize_topic(generated.topic)
        filename = minutes_writer.derive_minutes_filename(audio_path, generated.filename_slug)
        output_path = minutes_writer.resolve_minutes_output_path(
            transcript_path.parent, filename
        )
        content = minutes_writer.build_minutes_markdown(
            topic=topic_sanitized,
            body_markdown=generated.body_markdown,
            audio_path=audio_path,
            transcript_path=transcript_path,
            language=language,
            whisper_model=whisper_model,
            ollama_model=cfg.model,
        )
        minutes_writer.write_minutes(content, output_path)
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to write minutes file for %s", audio_path)
        _emit_log(on_log, "ERROR", f"議事録書き出し失敗: {e}")
        _emit_notify(notify, "議事録生成失敗", f"{audio_path.name}: {str(e)[:120].replace(chr(10), ' ')}")
        return None

    _emit_log(on_log, "INFO", f"Saved minutes: {output_path}")
    _emit_notify(notify, "議事録生成完了", output_path.name)
    return MinutesRunResult(output_path=output_path, topic_sanitized=topic_sanitized)
