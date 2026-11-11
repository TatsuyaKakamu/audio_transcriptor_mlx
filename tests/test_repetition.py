import pytest

from app.services.repetition import MARKER, collapse_repetitions


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("最初に" + "お客様の" * 10 + "対応です", "最初にお客様の" + MARKER + "対応です"),
        ("結果としては、" + " 結果としては、" * 5, "結果としては、" + MARKER),
        ("気まぐれプリンス" + " 気まぐれプリンス" * 5, "気まぐれプリンス" + MARKER),
        ("あ" * 12, "あ" + MARKER),
        ("お願いします" * 5, "お願いします" + MARKER),
        ("はい、" * 10, "はい、" + MARKER),
        # A cut-off start of the phrase at the end of the text is part of the loop.
        ("車の中で、" * 6 + "車の", "車の中で、" + MARKER),
        ("the end " * 5 + "of it", "the end" + MARKER + " of it"),
        ("番号は" + "、54" * 12, "番号は、54" + MARKER),
        ("エラーにはならないんですけど、" * 3, "エラーにはならないんですけど、" + MARKER),
        ("あ" + "〜" * 40, "あ〜" + MARKER),
    ],
)
def test_collapses_loops(text, expected):
    assert collapse_repetitions(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "そうかそうかそうか、だから",
        "はい、はい、はい、はい",
        "そうですね、そうですね、そうですね",
        "予算は1000000円です",
        "予算は10000000000000円です",
        "1、2、1、2、1、2",
        "〜〜〜〜〜〜〜〜〜〜〜〜",
        "えーーーーーーーー、そうですね",
        "あはははははははは楽しい",
        "very very very very good",
        "ありがとうありがとうありがとうありがとう",
        "はい、はい、はい、はい、はい",
        "そうそうそうそうそうそうそう",
        "はいはいはいはいはいはいはいはい",
        "ーーー",
        "0" * 20,
        "そうなんですよそうなんですよ",
        "",
    ],
)
def test_keeps_natural_repetition(text):
    assert collapse_repetitions(text) == text


def test_cut_off_phrase_followed_by_more_text_is_kept():
    text = "お客様の" * 6 + "お客様からの連絡"
    assert collapse_repetitions(text) == "お客様の" + MARKER + "お客様からの連絡"


def test_collapses_each_separate_loop():
    text = "A社は" + "問題の受け付け、" * 5 + "そして" + "東京都市の" * 5
    assert collapse_repetitions(text) == "A社は問題の受け付け、" + MARKER + "そして東京都市の" + MARKER


def test_long_non_loop_run_is_fast():
    import time

    start = time.monotonic()
    collapse_repetitions("a" + "、" * 20000)
    assert time.monotonic() - start < 1.0
