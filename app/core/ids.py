"""ID 生成：ULID（26 位 Crockford Base32，时间有序）。

为什么不用 uuid4：``chat_sessions.id`` 是 CHAR(26) 且列表按"最近活跃"排序，
ULID 前 10 位编码毫秒时间戳，天然按创建时间有序，建索引后范围扫描即有序，
省掉一次 ``ORDER BY created_at`` 的排序开销（设计方案 §2.2）。

实现遵循 ULID 规范（https://github.com/ulid/spec）：
48 bit 时间戳(ms) + 80 bit 随机数，Crockford Base32 编码，共 26 字符。
"""

from __future__ import annotations

import os
import time

#: Crockford Base32（去掉 I L O U，避免人工识别歧义）
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_LEN = 10  # 48 bit / 5 bit ≈ 9.6 → 10 字符
_RANDOM_LEN = 16  # 80 bit / 5 bit = 16 字符


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(timestamp_ms: int | None = None) -> str:
    """生成 ULID 字符串（26 字符，大写）。"""
    ms = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    randomness = int.from_bytes(os.urandom(10), "big")  # 80 bit
    return _encode(ms, _TIME_LEN) + _encode(randomness, _RANDOM_LEN)


def ulid_timestamp_ms(ulid: str) -> int:
    """从 ULID 反解毫秒时间戳（调试/测试用）。"""
    if len(ulid) < _TIME_LEN:
        raise ValueError(f"非法 ULID: {ulid!r}")
    value = 0
    for ch in ulid[:_TIME_LEN]:
        value = value * 32 + _ALPHABET.index(ch.upper())
    return value
