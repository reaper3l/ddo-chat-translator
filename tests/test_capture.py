"""画面变化检测测试（决定这一帧要不要跑 OCR 的那一步）。"""
from app import capture


def _frame():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (200, 100), (18, 20, 26))
    ImageDraw.Draw(image).text((5, 5), "hello chat", fill=(230, 230, 230))
    return image


def test_same_frame_is_unchanged():
    first = capture.frame_signature(_frame())
    second = capture.frame_signature(_frame())
    assert first and second
    assert capture.frames_differ(first, second) is False


def test_new_chat_line_counts_as_changed():
    from PIL import ImageDraw

    before = _frame()
    after = _frame()
    ImageDraw.Draw(after).text((5, 40), "(小队): [小队] Ize: omw", fill=(126, 231, 135))
    assert capture.frames_differ(capture.frame_signature(before),
                                 capture.frame_signature(after)) is True


def test_single_pixel_blink_is_ignored():
    from PIL import ImageDraw

    before = _frame()
    after = _frame()
    ImageDraw.Draw(after).point((3, 3), fill=(255, 255, 255))
    assert capture.frames_differ(capture.frame_signature(before),
                                 capture.frame_signature(after)) is False


def test_missing_signature_means_changed():
    """拿不到指纹时宁可跑一次 OCR，也不要漏掉新消息。"""
    assert capture.frames_differ(b"", b"abc") is True
    assert capture.frames_differ(b"abc", b"") is True


def _chat_shot(brightness_shift: int = 0, extra_line: bool = False):
    """模拟 DDO 聊天框：深色半透明底 + 几行亮字，背景整体可以亮/暗一档。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (220, 90), (18, 20, 26))
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(("(小队): [小队] Ize: omw",
                                  "(小队): [小队] Ize: ty",
                                  "(小队):Dorgeth已断线")):
        draw.text((5, 6 + index * 22), text, fill=(230, 230, 230))
    if extra_line:
        draw.text((5, 72), "(小队): [小队] Guihuo: in", fill=(126, 231, 135))
    if brightness_shift:
        image = image.point(lambda value: max(0, min(255, value + brightness_shift)))
    return image


def test_background_brightness_change_is_not_a_change():
    """半透明背景一亮一暗（背后景物在动），不该被当成"画面变了"。

    这是玩家反馈的问题：背景一变就被判成新画面 → 白跑 OCR，还会把同一行
    读出细微差异、变成重复消息。帧指纹现在先做自动对比度归一化，
    整体亮度变化不影响判断。
    """
    plain = capture.frame_signature(_chat_shot())
    brighter = capture.frame_signature(_chat_shot(brightness_shift=40))
    darker = capture.frame_signature(_chat_shot(brightness_shift=-12))
    assert plain and brighter and darker
    assert capture.frames_differ(plain, brighter) is False
    assert capture.frames_differ(plain, darker) is False


def test_raw_grayscale_would_have_called_it_changed():
    """对照：如果不做归一化（旧做法），同样的亮度变化会被判成"变了"。"""
    from app import frame as frame_module
    from PIL import Image

    def raw(image):
        resample = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
        return image.convert("L").resize((48, 48), resample).tobytes()

    changed, _first, _last = frame_module.analyse_frame(
        raw(_chat_shot()), raw(_chat_shot(brightness_shift=40)),
        tolerance=6, threshold=6)
    assert changed is True


def test_new_line_still_counts_as_changed_after_normalising():
    """归一化不能把"真的多了新行"也吃掉。"""
    before = capture.frame_signature(_chat_shot())
    after = capture.frame_signature(_chat_shot(brightness_shift=40, extra_line=True))
    assert capture.frames_differ(before, after) is True
