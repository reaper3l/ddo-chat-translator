"""Ed25519 签名/验签（RFC 8032）—— 只为了让"自动更新"能验证发布包是不是你签的。

为什么自己写、不加依赖：

* 这个项目不想再多一个第三方密码库（打包体积、版本兼容、审查成本）；
* 验签是**低频**动作（每次升级验一个包），纯 Python 的慢速度完全够用；
* 代码短、可读，算法是公开标准（djb 的参考实现体系），而且有测试向量 +
  pycryptodome 交叉验证兜底（见 tests/test_ed25519.py）。

**它不是用来加密的**，只做签名/验签；私钥永远不参与发布流程之外的事情。
"""
from __future__ import annotations

import hashlib

_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _Q - 2, _Q) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_BY = 4 * pow(5, _Q - 2, _Q) % _Q
_BX = _xrecover(_BY)
_B = (_BX % _Q, _BY % _Q, 1, (_BX * _BY) % _Q)


def _edwards_add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _Q
    b = (y1 + x1) * (y2 + x2) % _Q
    c = 2 * t1 * t2 * _D % _Q
    dd = 2 * z1 * z2 % _Q
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _Q, g * h % _Q, f * g % _Q, e * h % _Q)


def _scalarmult(point, scalar: int):
    result = (0, 1, 1, 0)
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _edwards_add(result, addend)
        addend = _edwards_add(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point) -> bytes:
    x, y, z, _t = point
    zi = pow(z, _Q - 2, _Q)
    x = (x * zi) % _Q
    y = (y * zi) % _Q
    return ((y | ((x & 1) << 255))).to_bytes(32, "little")


def _decode_point(data: bytes):
    if len(data) != 32:
        raise ValueError("点必须是 32 字节")
    value = int.from_bytes(data, "little")
    y = value & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (value >> 255) & 1:
        x = _Q - x
    point = (x, y, 1, (x * y) % _Q)
    return point


def _is_on_curve(point) -> bool:
    x, y, z, t = point
    return ((-x * x + y * y - z * z - _D * t * t) % _Q) == 0


def _mod_l(value: int) -> int:
    return value % _L


def _clamp(digest: bytes) -> int:
    """把私钥哈希的低 32 字节按 RFC 8032 修剪成标量。

    等价于 a[0]&=248; a[31]&=127; a[31]|=64 —— 也就是
    "低 3 位清零、最高位清零、第 254 位置 1"。
    """
    scalar = 2 ** 254
    for index in range(3, 254):
        if (digest[index // 8] >> (index % 8)) & 1:
            scalar += 2 ** index
    return scalar


def publickey(seed: bytes) -> bytes:
    """由 32 字节私钥种子算出 32 字节公钥。"""
    if len(seed) != 32:
        raise ValueError("私钥种子必须是 32 字节")
    digest = _sha512(seed)
    scalar = _clamp(digest)
    return _encode_point(_scalarmult(_B, scalar))


def sign(message: bytes, seed: bytes) -> bytes:
    """用 32 字节私钥种子对 message 签名，返回 64 字节签名。"""
    if len(seed) != 32:
        raise ValueError("私钥种子必须是 32 字节")
    digest = _sha512(seed)
    scalar = _clamp(digest)
    prefix = digest[32:]
    public = _encode_point(_scalarmult(_B, scalar))
    r = _mod_l(int.from_bytes(_sha512(prefix + message), "little"))
    big_r = _encode_point(_scalarmult(_B, r))
    h = _mod_l(int.from_bytes(_sha512(big_r + public + message), "little"))
    s = _mod_l(r + h * scalar)
    return big_r + s.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, public: bytes) -> bool:
    """验签：签名/公钥长度不对、点不在曲线上、校验失败都返回 False（不抛异常）。"""
    try:
        if len(signature) != 64 or len(public) != 32:
            return False
        big_r = _decode_point(signature[:32])
        point_a = _decode_point(public)
        if not _is_on_curve(big_r) or not _is_on_curve(point_a):
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        h = _mod_l(int.from_bytes(_sha512(signature[:32] + public + message), "little"))
        left = _scalarmult(_B, s)
        right = _edwards_add(big_r, _scalarmult(point_a, h))
        return _encode_point(left) == _encode_point(right)
    except Exception:
        return False
