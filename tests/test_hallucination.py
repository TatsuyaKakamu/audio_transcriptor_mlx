import pytest

from app.services.hallucination import is_silence_hallucination, is_stock_only


@pytest.mark.parametrize(
    "text",
    ["ご視聴ありがとうございました", "ご視聴ありがとうございました。", "Thank you.", "thank you for watching!",
     "Ｔｈａｎｋ　ｙｏｕ", "ありがとうございました…", "お疲れさまでした〜", "お疲れ様でした お疲れ様でした"],
)
def test_stock_only(text):
    assert is_stock_only(text)


@pytest.mark.parametrize(
    "text",
    ["", "。", "長い時間ありがとうございました", "はい、お疲れ様でした", "ありがとうございました、では次に",
     "Thank you, Tanaka-san."],
)
def test_not_stock_only(text):
    assert not is_stock_only(text)


@pytest.mark.parametrize(
    ("text", "duration", "no_speech", "window_has_speech", "expected"),
    [
        ("ご視聴ありがとうございました", 17.6, 0.1, False, True),  # a phrase over a long silent span
        ("ありがとうございました。", 2.0, 0.1, False, False),      # a real closing remark
        ("お疲れ様でした", 2.0, 0.8, False, True),                 # Whisper judged the window silent
        ("お疲れ様でした", 8.0, 0.0, False, True),
        ("お疲れ様でした", 2.0, 0.6, False, True),
        ("お疲れ様でした", 7.9, 0.59, False, False),
        ("お疲れ様でした", 20.0, 0.9, True, False),                # real speech in the same window
        ("それでは会議を終わります", 25.0, 0.9, False, False),      # not a stock phrase: never dropped
    ],
)
def test_is_silence_hallucination(text, duration, no_speech, window_has_speech, expected):
    assert is_silence_hallucination(text, duration, no_speech, window_has_speech) is expected
