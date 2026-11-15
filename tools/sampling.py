"""抽樣共用工具：加權選取、配額分配（從 gold/generate.py 0.1.0 搬出，行為不變）。"""
from __future__ import annotations

import random
from typing import Any


def pick_weighted(rng: random.Random, pairs: list[tuple[Any, float]]):
    r = rng.random(); acc = 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def allocate_quota(branches: list[dict], n: int) -> dict[str, int]:
    """按 quota 四捨五入分配題數；差額加減到 quota 最大的分支。與 generate.py 0.1.0 的 plan_branches 相同。"""
    counts = {b["id"]: int(round(b["quota"] * n)) for b in branches}
    diff = n - sum(counts.values())
    for b in sorted(branches, key=lambda b: -b["quota"])[: abs(diff)]:
        counts[b["id"]] += 1 if diff > 0 else -1
    return counts
