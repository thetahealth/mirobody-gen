"""Synthetic identifiers (visit, barcode, specimen numbers) with a verifiable checksum tail.

合成编号：门诊号、条码号、标本号。带一个**可验证的校验尾**。

检验单上总要印一串号码，而隐私闸门的 PII 谓词（"门诊号"后面跟六位以上数字）正是冲着这种串去的。
两个坏办法：不印号码（失真——`meta.subject_field_as_indicator` 这类陷阱就是号码被当成指标），
或者给闸门开个"out/ 目录不查"的口子（那闸门就只剩装饰作用）。

这里的办法：合成编号的最后三位是前面数字的**带盐哈希**。闸门独立重算一遍
（`audit/privacy.py` 里另写一份，不 import 这里——同一个函数跑两遍只能确认它自己的盲区），
对得上才放行，并计数打印。一串真实号码碰巧对上的概率是千分之一；
而真实号码进入产物还要先躲过 n-gram 回放检测。
"""

from __future__ import annotations

import hashlib
import random

SALT = "mirobody-gen/synthetic-id/v1"


def checksum(body: str) -> str:
    digest = hashlib.blake2b(f"{SALT}:{body}".encode(), digest_size=4).digest()
    return f"{int.from_bytes(digest, 'big') % 1000:03d}"


def make(rng: random.Random, digits: int = 10, prefix: str = "") -> str:
    """`digits` 是总位数（含三位校验尾）。`prefix` 是字母前缀，如条码的 `B`。"""
    body = "".join(str(rng.randrange(10)) for _ in range(digits - 3))
    if body[0] == "0":
        body = str(rng.randrange(1, 10)) + body[1:]
    return f"{prefix}{body}{checksum(body)}"

