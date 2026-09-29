"""OCR 结果过滤：背景花的时候，低置信度的行不能被当成聊天。"""
from PIL import Image

from app.ocr import OcrEngine


def _engine_with(rows, min_score=0.5):
    """造一个假的 OCR 引擎，直接喂识别结果（不加载模型、不跑推理）。"""
    engine = OcrEngine()
    engine._engine = lambda _image: (rows, 0.0)
    engine.set_min_score(min_score)
    return engine


def _box(text: str):
    """随便给一个框（只用来排序，不影响过滤）。"""
    return [[0, 0], [100, 0], [100, 20], [0, 20]]


def test_low_confidence_rows_are_dropped():
    engine = _engine_with([
        [_box("(小队): [小队] Ize: omw"), "(小队): [小队] Ize: omw", 0.95],
        [_box("(小队): 位面监"), "(小队): 位面监", 0.31],        # 背景噪声读出来的残缺行
        [_box("(小队):Dorgeth已断线"), "(小队):Dorgeth已断线", 0.88],
    ])
    texts = [text for text, _box_ in engine.recognize(Image.new("RGB", (200, 60)))]
    assert texts == ["(小队): [小队] Ize: omw", "(小队):Dorgeth已断线"]
    assert engine.dropped_low_score == 1


def test_threshold_zero_keeps_everything():
    engine = _engine_with([
        [_box("noise"), "noise", 0.1],
        [_box("(小队): hi"), "(小队): hi", 0.9],
    ], min_score=0)
    texts = [text for text, _box_ in engine.recognize(Image.new("RGB", (200, 60)))]
    assert texts == ["(小队): hi", "noise"] or texts == ["noise", "(小队): hi"]
    assert len(texts) == 2


def test_rows_without_score_are_kept():
    """引擎没给分数（老版本只有两项）时，不能因为"没分数"就丢掉。"""
    engine = _engine_with([[_box("(小队): hi"), "(小队): hi"]], min_score=0.5)
    texts = [text for text, _box_ in engine.recognize(Image.new("RGB", (200, 60)))]
    assert texts == ["(小队): hi"]


def test_set_min_score_clamps():
    engine = OcrEngine()
    engine.set_min_score(2)
    assert engine._min_score == 1.0
    engine.set_min_score(-1)
    assert engine._min_score == 0.0
    engine.set_min_score("abc")            # 脏值回到默认
    assert engine._min_score == 0.5
