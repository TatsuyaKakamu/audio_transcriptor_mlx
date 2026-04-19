from app.services.vad import remap_timestamp


def test_remap_single_interval():
    intervals = [(5.0, 10.0)]
    assert remap_timestamp(0.0, intervals) == 5.0
    assert remap_timestamp(2.5, intervals) == 7.5
    assert remap_timestamp(5.0, intervals) == 10.0


def test_remap_two_intervals():
    # original: [2-5) and [8-12)  → VAD timeline: [0-3) then [3-7)
    intervals = [(2.0, 5.0), (8.0, 12.0)]
    assert remap_timestamp(0.0, intervals) == 2.0
    assert remap_timestamp(3.0, intervals) == 8.0   # boundary: start of second interval
    assert remap_timestamp(5.0, intervals) == 10.0  # 2s into second interval


def test_remap_clamps_at_end():
    intervals = [(0.0, 3.0)]
    assert remap_timestamp(100.0, intervals) == 3.0


def test_remap_preserves_order():
    intervals = [(1.0, 4.0), (6.0, 9.0), (11.0, 14.0)]
    prev = -1.0
    for vad_t in [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 9.0]:
        orig = remap_timestamp(vad_t, intervals)
        assert orig >= prev, f"order violated at vad_t={vad_t}: {orig} < {prev}"
        prev = orig
