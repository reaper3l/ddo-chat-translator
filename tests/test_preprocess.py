"""背景压平测试：聊天框是半透明的，背后景物忽明忽暗时不能让识别跟着乱。"""
from app import capture, preprocess


def _shot(brightness_shift: int = 0, gradient: bool = False, extra_line: bool = False):
    """合成一张"聊天框"：深色底 + 几行亮字；背景可以整体变亮，也可以一侧偏亮。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (300, 120), (30, 34, 42))
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(("(小队): [小队] Ize: omw",
                                  "(小队): [小队] Ize: ty",
                                  "(小队): Dorgeth 已断线")):
        draw.text((6, 8 + index * 26), text, fill=(190, 190, 190))
    if extra_line:
        draw.text((6, 100), "(小队): [小队] Guihuo: in", fill=(180, 190, 190))
    if gradient:
        # 右边逐渐变亮：模拟亮的东西从聊天框后面扫过。
        # 注意用"加法"而不是"变淡" —— 半透明背景只是往画面上叠一层亮度，
        # 文字本身的对比度不会被拉低。
        import numpy as np

        values = np.asarray(image).astype(np.int16)
        ramp = np.linspace(0, 40, image.width)[None, :, None]
        image = Image.fromarray(np.clip(values + ramp, 0, 255).astype(np.uint8))
    if brightness_shift:
        image = image.point(lambda v: max(0, min(255, v + brightness_shift)))
    return image


def test_radius_stays_in_sane_range():
    assert preprocess.radius_for(443, 205) == 7
    assert preprocess.radius_for(20, 20) == 4
    assert preprocess.radius_for(4000, 4000) == 12
    assert preprocess.radius_for(0, 0) == 4


def test_flatten_puts_flat_background_on_mid_gray():
    from PIL import Image

    flat = preprocess.flatten(Image.new("RGB", (80, 40), (30, 34, 42)))
    values = set(flat.tobytes())
    assert len(values) == 1
    assert abs(next(iter(values)) - 128) <= 1


def test_flatten_is_immune_to_overall_brightness():
    """整张图变亮/变暗，压平后的结果应该几乎一样。"""
    base = _shot()
    flatter = preprocess.flatten(_shot(brightness_shift=50))
    darker = preprocess.flatten(_shot(brightness_shift=-20))
    original = preprocess.flatten(base)
    for other in (flatter, darker):
        diff = max(abs(a - b) for a, b in zip(original.tobytes(), other.tobytes()))
        assert diff <= 4, diff


def test_flatten_keeps_text_strokes_visible():
    """压平不能把字也一起压没了。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (200, 60), (30, 34, 42))
    ImageDraw.Draw(image).rectangle([20, 26, 160, 34], fill=(220, 220, 220))
    flat = preprocess.flatten(image)
    assert max(flat.tobytes()) > 170


def test_flatten_handles_bad_input():
    from PIL import Image

    assert preprocess.flatten(None) is None
    assert preprocess.flatten(Image.new("RGB", (1, 1), (0, 0, 0))) is not None


def test_signature_ignores_background_brightness_change():
    plain = capture.frame_signature(_shot())
    brighter = capture.frame_signature(_shot(brightness_shift=50))
    darker = capture.frame_signature(_shot(brightness_shift=-20))
    assert capture.frames_differ(plain, brighter) is False
    assert capture.frames_differ(plain, darker) is False


def test_signature_tolerates_local_brightness_gradient():
    """亮的东西从聊天框后面扫过（一侧变亮）：压平后不该判成"画面变了"。"""
    plain = capture.frame_signature(_shot())
    gradient = capture.frame_signature(_shot(gradient=True))
    assert capture.frames_differ(plain, gradient) is False


def test_signature_still_detects_a_new_line():
    """压平不能把"真的来了新消息"也吃掉。"""
    before = capture.frame_signature(_shot())
    after = capture.frame_signature(_shot(brightness_shift=50, extra_line=True))
    assert capture.frames_differ(before, after) is True


def test_old_signature_behaviour_can_be_switched_off():
    """关掉压平时退回老做法（整图自动对比度），接口还在。"""
    image = _shot()
    assert capture.frame_signature(image, flatten=False)
    assert capture.frame_signature(image, flatten=True)


def test_ocr_engine_can_toggle_flatten():
    from app.ocr import OcrEngine

    engine = OcrEngine()
    engine.set_flatten(False)
    assert engine._flatten is False
    engine.set_flatten(True)
    assert engine._flatten is True


def test_ocr_engine_can_toggle_det_cap():
    """检测尺寸限幅（只缩不放）也要能开关，默认开。"""
    from app.ocr import OcrEngine

    engine = OcrEngine()
    assert engine._det_cap is True
    engine.set_det_cap(False)
    assert engine._det_cap is False
