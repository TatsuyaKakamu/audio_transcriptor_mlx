from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPException

from app.config import MinutesConfig
from app.models.types import TranscriptionResult

logger = logging.getLogger(__name__)


class MinutesGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedMinutes:
    topic: str
    body_markdown: str


def transcript_plain_text(result: TranscriptionResult) -> str:
    return "\n".join(seg.text for seg in result.segments if seg.text)


_SYSTEM_PROMPT_JA = """あなたは熟練した会議書記です。日本語の会議書き起こしを読み、構造化された議事録を作成します。出力は必ず JSON のみで、以下のスキーマに厳密に従ってください:

{
  "topic": "議題を端的に表す15文字以内の日本語タイトル(ファイル名に使用)",
  "minutes_markdown": "# <議題>\\n\\n## 概要\\n...\\n\\n## 決定事項\\n- ...\\n\\n## アクションアイテム\\n- [ ] 担当: 内容\\n\\n## 詳細\\n..."
}

制約:
- topic にはスラッシュ、コロン、改行、引用符を含めない
- minutes_markdown は Markdown として正しい
- 決定事項とアクションアイテムが無い場合は「特になし」と書く
- 推測ではなく書き起こしに含まれる事実のみを記載する"""

_USER_PROMPT_JA = """以下は会議の書き起こしです。これを基に議事録を JSON で出力してください。

----- 書き起こし開始 -----
{transcript}
----- 書き起こし終了 -----"""

_SYSTEM_PROMPT_EN = """You are an experienced meeting secretary. Read the meeting transcript and produce structured minutes. Output ONLY JSON conforming strictly to this schema:

{
  "topic": "A short title (<= 15 chars) describing the meeting topic; used as a filename",
  "minutes_markdown": "# <topic>\\n\\n## Summary\\n...\\n\\n## Decisions\\n- ...\\n\\n## Action Items\\n- [ ] Owner: content\\n\\n## Details\\n..."
}

Constraints:
- topic must not contain slashes, colons, newlines, or quotes
- minutes_markdown must be valid Markdown
- If there are no decisions or action items, write "None"
- Use only facts present in the transcript; do not speculate"""

_USER_PROMPT_EN = """Below is a meeting transcript. Produce minutes as JSON.

----- TRANSCRIPT BEGIN -----
{transcript}
----- TRANSCRIPT END -----"""


def _strip_json_fence(s: str) -> str:
    """Remove ```json ... ``` fences that some models add despite format:json."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _build_prompts(transcript_text: str, prompt_language: str) -> tuple[str, str]:
    if prompt_language == "en":
        return _SYSTEM_PROMPT_EN, _USER_PROMPT_EN.format(transcript=transcript_text)
    return _SYSTEM_PROMPT_JA, _USER_PROMPT_JA.format(transcript=transcript_text)


def _http_post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def generate_minutes(
    transcript_text: str,
    *,
    language: str,  # noqa: ARG001 — reserved for future per-language tweaks
    cfg: MinutesConfig,
) -> GeneratedMinutes:
    text = transcript_text or ""
    if len(text) > cfg.max_input_chars:
        logger.warning(
            "transcript length %d exceeds max_input_chars %d; truncating",
            len(text),
            cfg.max_input_chars,
        )
        text = text[: cfg.max_input_chars]

    system_prompt, user_prompt = _build_prompts(text, cfg.prompt_language)

    url = cfg.ollama_host.rstrip("/") + "/api/generate"
    options: dict[str, object] = {"temperature": 0.2}
    if cfg.num_ctx > 0:
        options["num_ctx"] = cfg.num_ctx
    payload = {
        "model": cfg.model,
        "prompt": user_prompt,
        "system": system_prompt,
        "stream": False,
        "format": "json",
        "options": options,
    }

    try:
        response = _http_post_json(url, payload, cfg.request_timeout_seconds)
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException, UnicodeDecodeError) as e:
        raise MinutesGenerationError(f"Ollama request failed: {e}") from e
    except json.JSONDecodeError as e:
        raise MinutesGenerationError(f"Ollama returned non-JSON envelope: {e}") from e

    raw = response.get("response")
    if not isinstance(raw, str) or not raw.strip():
        raise MinutesGenerationError("Ollama response missing 'response' field")

    try:
        parsed = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as e:
        raise MinutesGenerationError(f"Ollama 'response' is not valid JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise MinutesGenerationError(f"Ollama 'response' is not an object: {type(parsed).__name__}")

    topic = parsed.get("topic")
    body = parsed.get("minutes_markdown")
    if not isinstance(topic, str) or not topic.strip():
        raise MinutesGenerationError("missing or empty 'topic' in LLM response")
    if not isinstance(body, str) or not body.strip():
        raise MinutesGenerationError("missing or empty 'minutes_markdown' in LLM response")

    return GeneratedMinutes(topic=topic.strip(), body_markdown=body.strip())
