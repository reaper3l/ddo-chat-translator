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
