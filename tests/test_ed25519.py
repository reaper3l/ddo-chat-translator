"""Ed25519 实现的自检：RFC 8032 标准向量 + 往返签名 + 篡改检测。

装了 pycryptodome 的话再做一次交叉验证（两个独立实现算出同样的结果才敢用）。
"""
import base64

from app import ed25519

# RFC 8032 §7.1 的测试向量
SEED_1 = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
PUB_1 = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
SIG_1 = ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
         "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
SEED_2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
SIG_2 = ("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
         "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")


def test_rfc8032_vector_1():
    assert ed25519.publickey(SEED_1).hex() == PUB_1
    assert ed25519.sign(b"", SEED_1).hex() == SIG_1
    assert ed25519.verify(bytes.fromhex(SIG_1), b"", bytes.fromhex(PUB_1))


def test_rfc8032_vector_2():
    assert ed25519.sign(bytes.fromhex("72"), SEED_2).hex() == SIG_2


def test_round_trip_and_tamper_detection():
    seed = bytes(range(32))
    public = ed25519.publickey(seed)
    message = b"ddo-update-v1\n3.0.20\nDDO.zip\nabc\n"
    signature = ed25519.sign(message, seed)
    assert ed25519.verify(signature, message, public)
    # 改消息 / 改签名 / 换公钥 / 长度不对，都必须拒绝
    assert not ed25519.verify(signature, message + b"x", public)
    broken = bytearray(signature)
    broken[0] ^= 0x01
    assert not ed25519.verify(bytes(broken), message, public)
    assert not ed25519.verify(signature, message, ed25519.publickey(bytes(range(1, 33))))
    assert not ed25519.verify(signature[:63], message, public)
    assert not ed25519.verify(signature, message, public[:16])


def test_matches_pycryptodome_if_available():
    """两个独立实现（本模块 / pycryptodome）必须给出同样的结果。"""
    try:
        from Crypto.PublicKey import ECC
        from Crypto.Signature import eddsa
    except Exception:
        return                     # 没装就跳过（不是错误）

    seed = bytes(range(32, 64))
    key = ECC.construct(curve="Ed25519", seed=seed)
    assert key.public_key().export_key(format="raw") == ed25519.publickey(seed)
    # 我签的，它能验
    eddsa.new(ECC.import_key(key.public_key().export_key(format="PEM")),
              "rfc8032").verify(b"hello", ed25519.sign(b"hello", seed))
    # 它签的，我能验
    assert ed25519.verify(bytes(eddsa.new(key, "rfc8032").sign(b"hello")), b"hello",
                          ed25519.publickey(seed))


def test_base64_round_trip_helper():
    """签名工具把签名存成 base64 字符串，这里确认编解码没问题。"""
    seed = bytes(range(40, 72))
    signature = ed25519.sign(b"msg", seed)
    text = base64.b64encode(signature).decode("ascii")
    assert ed25519.verify(base64.b64decode(text), b"msg", ed25519.publickey(seed))
