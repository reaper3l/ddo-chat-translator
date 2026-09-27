"""帧间差异 / "只识别变化区域"的纯逻辑测试。"""
from app import frame


def _signature(changed_rows=(), grid=48):
    data = bytearray([100] * (grid * grid))
    for row in changed_rows:
        for col in range(grid):
            data[row * grid + col] = 200
    return bytes(data)


def test_unchanged_frame():
    same = _signature()
    changed, first, last = frame.analyse_frame(same, same)
    assert changed is False
    assert (first, last) == (-1, -1)


def test_changed_rows_detected():
    changed, first, last = frame.analyse_frame(_signature([40, 41, 42]), _signature())
    assert changed is True
    assert (first, last) == (40, 42)


def test_single_pixel_noise_is_ignored():
    noisy = bytearray(_signature())
    noisy[10 * 48 + 5] = 255          # 只有一个像素变化
    changed, _first, _last = frame.analyse_frame(bytes(noisy), _signature())
    assert changed is False


def test_missing_signature_counts_as_changed():
    changed, _first, _last = frame.analyse_frame(b"", _signature())
    assert changed is True


def test_band_at_bottom_is_small():
    start, end = frame.band_pixels((40, 47), 552)
    assert 0 < start < 552
    assert end == 552
    assert (end - start) < 552 * 0.5          # 只需识别下半部分


def test_band_includes_overlap():
    start, end = frame.band_pixels((24, 25), 480)
    assert start < int(24 * 480 / 48)         # 起点比变化行更靠上（留了余量）
    assert end > int(26 * 480 / 48)           # 终点也更靠下


def test_keep_lines_above_drops_overlapping():
    lines = [(10.0, 30.0, "a"), (40.0, 60.0, "b"), (70.0, 90.0, "c")]
    kept = frame.keep_lines_above(lines, 50.0)
    assert [text for _top, _bottom, text in kept] == ["a"]


def test_merge_lines_keeps_order():
    previous = [(10.0, 30.0, "a"), (40.0, 60.0, "b")]
    fresh = [(50.0, 70.0, "c")]
    merged = frame.merge_lines(previous, fresh, 50.0)
    assert [text for _top, _bottom, text in merged] == ["a", "c"]
