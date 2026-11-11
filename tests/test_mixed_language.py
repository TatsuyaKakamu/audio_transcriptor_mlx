import numpy as np

from app.services.mixed_language import (
    group_by_language,
    resolve_languages,
    slice_intervals,
    split_long_intervals,
)

SR = 16_000


def test_split_keeps_short_intervals():
    audio = np.ones(SR * 40, dtype=np.float32)
    assert split_long_intervals(audio, [(0.0, 10.0), (12.0, 30.0)]) == [(0.0, 10.0), (12.0, 30.0)]


def test_split_long_interval_at_quietest_point():
    audio = np.ones(SR * 70, dtype=np.float32)
    audio[int(22.0 * SR) : int(22.1 * SR)] = 0.0  # quiet frame inside [15, 30)
    pieces = split_long_intervals(audio, [(0.0, 50.0)])
    assert pieces[0][0] == 0.0
    assert abs(pieces[0][1] - 22.05) < 0.06
    assert pieces[-1][1] == 50.0
    assert all(e - s <= 30.0 for s, e in pieces)
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))


def test_split_whole_file_fallback_covers_everything():
    audio = np.random.default_rng(0).standard_normal(SR * 125).astype(np.float32)
    pieces = split_long_intervals(audio, [(0.0, 125.0)])
    assert pieces[0][0] == 0.0 and pieces[-1][1] == 125.0
    assert all(e - s <= 30.0 for s, e in pieces)


def test_resolve_picks_higher_of_ja_en():
    probs = [{"ja": 0.9, "en": 0.05}, {"ja": 0.1, "en": 0.8}]
    assert resolve_languages([5.0, 5.0], probs) == ["ja", "en"]


def test_resolve_ignores_other_languages():
    probs = [{"hi": 0.9, "en": 0.08, "ja": 0.01}]
    assert resolve_languages([5.0], probs) == ["en"]


def test_resolve_inherits_for_short_failed_or_ambiguous():
    probs = [
        {"ja": 0.1, "en": 0.9},
        {"ja": 0.9, "en": 0.0},  # too short
        None,  # detection failed
        {"ja": 0.52, "en": 0.48},  # ambiguous
        {"ja": 0.95, "en": 0.01},
    ]
    assert resolve_languages([5.0, 0.3, 5.0, 5.0, 5.0], probs) == ["en", "en", "en", "en", "ja"]


def test_resolve_leading_unreliable_takes_first_reliable():
    probs = [None, {"ja": 0.05, "en": 0.9}]
    assert resolve_languages([5.0, 5.0], probs) == ["en", "en"]


def test_resolve_all_unreliable_defaults_to_ja():
    assert resolve_languages([0.3, 5.0], [None, None]) == ["ja", "ja"]


def test_group_merges_consecutive_same_language_up_to_cap():
    intervals = [(0.0, 10.0), (11.0, 21.0), (22.0, 32.0), (33.0, 35.0), (36.0, 40.0)]
    langs = ["ja", "ja", "ja", "en", "ja"]
    assert group_by_language(intervals, langs, max_sec=25.0) == [
        ("ja", [(0.0, 10.0), (11.0, 21.0)]),
        ("ja", [(22.0, 32.0)]),
        ("en", [(33.0, 35.0)]),
        ("ja", [(36.0, 40.0)]),
    ]


def test_slice_intervals_concatenates():
    audio = np.arange(SR * 4, dtype=np.float32)
    out = slice_intervals(audio, [(0.0, 1.0), (2.0, 3.0)])
    assert len(out) == 2 * SR
    assert out[SR] == audio[2 * SR]


def test_resolve_switch_margin_is_stricter_for_en():
    probs = [{"ja": 0.9, "en": 0.05}, {"ja": 0.35, "en": 0.65}, {"ja": 0.1, "en": 0.9}]
    assert resolve_languages([5.0] * 3, probs, switch_margin=0.5) == ["ja", "ja", "en"]


def test_resolve_switch_margin_does_not_affect_ja():
    probs = [{"ja": 0.05, "en": 0.9}, {"ja": 0.65, "en": 0.35}]
    assert resolve_languages([5.0] * 2, probs, switch_margin=0.5) == ["en", "ja"]


def test_resolve_ambiguous_ja_leaning_falls_back_to_ja_when_strict():
    probs = [{"ja": 0.0, "en": 0.9}, {"ja": 0.52, "en": 0.48}, None]
    assert resolve_languages([5.0, 5.0, 5.0], probs, switch_margin=0.5) == ["en", "ja", "ja"]


def test_resolve_ambiguous_en_leaning_inherits_when_strict():
    probs = [{"ja": 0.0, "en": 0.9}, {"ja": 0.35, "en": 0.65}, {"ja": 0.9, "en": 0.0},
             {"ja": 0.35, "en": 0.65}]
    assert resolve_languages([5.0] * 4, probs, switch_margin=0.5) == ["en", "en", "ja", "ja"]


def test_resolve_short_or_failed_still_inherit_en_when_strict():
    probs = [{"ja": 0.0, "en": 0.9}, {"ja": 0.9, "en": 0.0}, None]
    assert resolve_languages([5.0, 0.3, 5.0], probs, switch_margin=0.5) == ["en", "en", "en"]
