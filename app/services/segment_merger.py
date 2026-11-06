from app.models.types import Segment

_SENTENCE_END = frozenset("。！？.!?")

# Languages written without spaces, where English fragments still need one.
_SPACELESS_LANGUAGES = frozenset({"ja", "mixed"})


def _is_ascii_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _join(texts: list[str], language: str) -> str:
    if language not in _SPACELESS_LANGUAGES:
        return " ".join(texts)
    joined = ""
    for text in texts:
        if joined and text and _is_ascii_word_char(text[0]) and (
            _is_ascii_word_char(joined[-1]) or joined[-1] == ","
        ):
            joined += " "
        joined += text
    return joined


def merge_by_conversation(
    segments: list[Segment],
    *,
    silence_gap_sec: float = 0.8,
    max_block_sec: float = 30.0,
    language: str = "ja",
) -> list[Segment]:
    if not segments:
        return []

    result: list[Segment] = []
    block_start = segments[0].start_sec
    block_texts: list[str] = [segments[0].text.strip()]
    block_end = segments[0].end_sec

    for prev, curr in zip(segments, segments[1:]):
        block_len = curr.end_sec - block_start
        gap = curr.start_sec - prev.end_sec
        ends_sentence = bool(prev.text.strip()) and prev.text.strip()[-1] in _SENTENCE_END

        if ends_sentence or gap >= silence_gap_sec or block_len > max_block_sec:
            result.append(Segment(block_start, block_end, _join(block_texts, language)))
            block_start = curr.start_sec
            block_texts = [curr.text.strip()]
        else:
            block_texts.append(curr.text.strip())

        block_end = curr.end_sec

    result.append(Segment(block_start, block_end, _join(block_texts, language)))
    return result
