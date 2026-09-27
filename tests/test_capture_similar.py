"""截图一致性校验测试（决定要不要用"快速抓图"的那道闸）。"""
from app import capture


def _image(color, size=(120, 80)):
    from PIL import Image

    return Image.new("RGB", size, color)


def test_identical_images_are_similar():
    assert capture.images_similar(_image((20, 22, 28)), _image((20, 22, 28))) is True


def test_slightly_different_images_are_still_similar():
    from PIL import ImageDraw

    first = _image((20, 22, 28))
    second = _image((20, 22, 28))
    ImageDraw.Draw(second).rectangle([0, 0, 30, 10], fill=(40, 44, 52))
    assert capture.images_similar(first, second) is True


def test_very_different_images_are_not_similar():
    assert capture.images_similar(_image((20, 22, 28)), _image((240, 240, 240))) is False


def test_missing_image_is_not_similar():
    assert capture.images_similar(None, _image((20, 22, 28))) is False
