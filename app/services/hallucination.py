"""Detect stock phrases Whisper writes into silent stretches of audio."""

import re
import unicodedata

# Phrases Whisper invents for silence (learned from video subtitles). People also
# say most of them, so a segment is dropped only with other evidence (see below).
STOCK_PHRASES = (
    "最後までご視聴いただきありがとうございました",
    "ご視聴ありがとうございました",
    "ありがとうございました",
    "お疲れ様でした",
    "お疲れさまでした",
    "おやすみなさい",
    "お待ちしております",
    "チャンネル登録をお願いします",
    "thankyouforwatching",
    "thanksforwatching",
    "thankyou",
)

# Said aloud these take about 2 s. In the 2026 transcripts of this project, 70 % of
# stock-only lines were under 3 s, while "ご視聴ありがとうございました" lines (never
# said in a meeting) had a median span of 17.6 s: a phrase stretched over silence.
MIN_SUSPICIOUS_SEC = 8.0
# Same value as transcriber's no_speech_threshold: windows above it that mlx-whisper
# still kept were judged silent but decoded with confidence, the hallucination case.
MIN_NO_SPEECH_PROB = 0.6

_IGNORED = re.compile(r"[\s。、.,!?「」『』()…〜~ー・♪\-]+")
_STOCK_ONLY = re.compile("(?:%s)+" % "|".join(re.escape(p) for p in STOCK_PHRASES))


def is_stock_only(text: str) -> bool:
    core = _IGNORED.sub("", unicodedata.normalize("NFKC", text)).lower()
    return bool(core) and _STOCK_ONLY.fullmatch(core) is not None


def is_silence_hallucination(
    text: str, duration_sec: float, no_speech_prob: float, window_has_speech: bool
) -> bool:
    """`window_has_speech`: other text was decoded from the same 30 s window.

    no_speech_prob is one value per window, and a long span can be a real closing
    remark stretched to the window end, so neither is trusted next to real speech.
    """
    if window_has_speech or not is_stock_only(text):
        return False
    return duration_sec >= MIN_SUSPICIOUS_SEC or no_speech_prob >= MIN_NO_SPEECH_PROB
