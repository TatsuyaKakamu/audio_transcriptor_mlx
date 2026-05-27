from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

_FORBIDDEN_CHARS = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")
_WHITESPACE_RUN = re.compile(r"\s+")
_SLUG_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]")
_FALLBACK_TOPIC = "議題未取得"
_FALLBACK_SLUG = "minutes"


def sanitize_topic(topic: str, *, max_len: int = 60) -> str:
    if not isinstance(topic, str):
        return _FALLBACK_TOPIC
    cleaned = _WHITESPACE_RUN.sub("_", topic)
    cleaned = _FORBIDDEN_CHARS.sub("", cleaned)
    cleaned = cleaned.strip(" ._")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" ._")
    return cleaned or _FALLBACK_TOPIC


def sanitize_slug(slug: str, *, max_len: int = 60) -> str:
    """Force an ASCII-only filename slug; non-English characters are dropped."""
    if not isinstance(slug, str):
        return _FALLBACK_SLUG
    cleaned = _WHITESPACE_RUN.sub("_", slug.strip())
    cleaned = _SLUG_DISALLOWED.sub("", cleaned)
    cleaned = cleaned.strip("._-")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].strip("._-")
    return cleaned or _FALLBACK_SLUG


def derive_minutes_filename(audio_path: Path, slug: str) -> str:
    mtime = audio_path.stat().st_mtime
    date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    return f"{date_str}_{sanitize_slug(slug)}.md"


def resolve_minutes_output_path(directory: Path, base_name: str) -> Path:
    if not base_name.endswith(".md"):
        raise ValueError(f"base_name must end with .md: {base_name!r}")
    stem = base_name[: -len(".md")]
    candidate = directory / f"{stem}.md"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = directory / f"{stem}.{n}.md"
        if not candidate.exists():
            return candidate
        n += 1


def build_minutes_markdown(
    *,
    topic: str,
    body_markdown: str,
    audio_path: Path,
    transcript_path: Path,
    language: str,
    whisper_model: str,
    ollama_model: str,
) -> str:
    mtime = audio_path.stat().st_mtime
    date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    body = body_markdown.strip()
    frontmatter = "\n".join(
        [
            "---",
            f"date: {date_str}",
            f"source_audio: {audio_path.name}",
            f"transcript: {transcript_path.name}",
            f"language: {language}",
            f"whisper_model: {whisper_model}",
            f"ollama_model: {ollama_model}",
            f"topic: {topic}",
            "---",
        ]
    )
    footer = f"原文書き起こし: [{transcript_path.name}]({transcript_path.name})"
    return f"{frontmatter}\n\n{body}\n\n---\n{footer}\n"


def write_minutes(content: str, output_path: Path) -> None:
    output_path.write_text(content, encoding="utf-8")
