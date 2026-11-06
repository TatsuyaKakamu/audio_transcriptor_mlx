import pytest
from app.models.types import Segment
from app.services.segment_merger import merge_by_conversation


def seg(start, end, text):
    return Segment(start_sec=start, end_sec=end, text=text)


def test_empty():
    assert merge_by_conversation([]) == []


def test_single():
    s = seg(0.0, 1.0, "こんにちは。")
    assert merge_by_conversation([s]) == [s]


def test_merge_no_sentence_end_no_gap():
    segs = [
        seg(0.0, 1.0, "こんにちは"),
        seg(1.1, 2.0, "今日は"),
        seg(2.1, 3.0, "いい天気ですね。"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="ja")
    assert len(result) == 1
    assert result[0].start_sec == 0.0
    assert result[0].end_sec == 3.0
    assert result[0].text == "こんにちは今日はいい天気ですね。"


def test_split_on_sentence_end():
    segs = [
        seg(0.0, 1.0, "ありがとうございます。"),
        seg(1.1, 2.0, "次の話題に"),
        seg(2.1, 3.0, "移ります。"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="ja")
    assert len(result) == 2
    assert result[0].text == "ありがとうございます。"
    assert result[1].text == "次の話題に移ります。"


def test_split_on_silence_gap():
    segs = [
        seg(0.0, 1.0, "最初の発話"),
        seg(2.5, 3.5, "間があいた発話"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="ja")
    assert len(result) == 2


def test_no_split_below_gap_threshold():
    segs = [
        seg(0.0, 1.0, "短い間"),
        seg(1.5, 2.5, "続き"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="ja")
    assert len(result) == 1


def test_forced_split_on_max_block():
    segs = [
        seg(0.0, 15.0, "長い発話その一"),
        seg(15.1, 31.0, "長い発話その二"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, max_block_sec=30.0, language="ja")
    assert len(result) == 2


def test_english_space_join():
    segs = [
        seg(0.0, 1.0, "Hello"),
        seg(1.1, 2.0, "world"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="en")
    assert result[0].text == "Hello world"


def test_question_mark_splits():
    segs = [
        seg(0.0, 1.0, "本当ですか？"),
        seg(1.1, 2.0, "はい"),
    ]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language="ja")
    assert len(result) == 2


@pytest.mark.parametrize("language", ["ja", "mixed"])
def test_spaceless_join_adds_space_between_ascii_words(language):
    segs = [seg(0.0, 1.0, "We expect to finish it"), seg(1.1, 2.0, "by December")]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language=language)
    assert result[0].text == "We expect to finish it by December"


@pytest.mark.parametrize("language", ["ja", "mixed"])
def test_spaceless_join_after_comma(language):
    segs = [seg(0.0, 1.0, "Sure,"), seg(1.1, 2.0, "we did")]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language=language)
    assert result[0].text == "Sure, we did"


@pytest.mark.parametrize("language", ["ja", "mixed"])
def test_spaceless_join_keeps_japanese_and_boundaries_tight(language):
    segs = [seg(0.0, 1.0, "セキュア"), seg(1.1, 2.0, "ブート"), seg(2.1, 3.0, "SBOM"), seg(3.1, 4.0, "対応")]
    result = merge_by_conversation(segs, silence_gap_sec=0.8, language=language)
    assert result[0].text == "セキュアブートSBOM対応"
