"""中文數字與金額格式（從 gold/generate.py 0.1.0 搬出；行為必須與 0.1.0 完全相同）。"""
from __future__ import annotations

CN_DIGITS = "零一二三四五六七八九"


def _cn_below_10000(n: int) -> str:
    if n < 10:
        return CN_DIGITS[n]
    out, rest, zero_pending = "", n, False
    for v, u in ((1000, "千"), (100, "百"), (10, "十")):
        d, rest = divmod(rest, v)
        if d:
            if zero_pending:
                out += "零"; zero_pending = False
            out += ("" if d == 1 and u == "十" and not out else CN_DIGITS[d]) + u
        elif out:
            zero_pending = True
    if rest:
        if zero_pending:
            out += "零"
        out += CN_DIGITS[rest]
    return out


def int_to_cn(n: int) -> str:
    """整數 → 中文數字（到億以下）。"""
    if n < 10000:
        return _cn_below_10000(n)
    wan, rest = divmod(n, 10000)
    out = _cn_below_10000(wan) + "萬"
    if rest:
        if rest < 1000:
            out += "零"
        out += _cn_below_10000(rest)
    return out


def format_salary(n: int, style: str) -> str:
    if style == "arabic":
        return f"{n:,}"
    if style == "arabic_plain":
        return str(n)
    if style == "wan":  # 3萬5、3.5萬、35,000 混用
        if n % 10000 == 0:
            return f"{n // 10000}萬"
        if n % 1000 == 0:
            return f"{n // 10000}萬{(n % 10000) // 1000}"
        return f"{n:,}"
    if style == "cn":
        return int_to_cn(n)
    raise ValueError(style)
