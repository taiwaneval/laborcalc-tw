"""overtime_pay 抽樣器：月薪分布、分支規劃、模板欄位。

行為與 gold/generate.py 0.1.0 逐 bit 相同（tests/test_overtime_sampler.py 以 2026Q4 public 的 200 題重播驗證）。
契約（所有 family 的 sampler.py 共用，gold/generate.py 依此呼叫）：
  plan(spec, n, rng)            → list[dict]：每題的 branch、template_pool 與該 branch 固定的參數；
                                  可選 "knobs"（dict），generate.py 併入題目 knobs 與 coverage（例：annual_leave_days 的 anchor_months）
  sample(rng, planned)          → params（鍵 = spec.implementation.arg_order），只用傳入的 rng
  context(rng, params, tpl)     → (模板欄位, knobs)；姓名由 generate.py 抽
  visible(params, fields)       → list[set[str]]：題幹對每個集合至少命中一個（gate）
  coverage_keys(params)         → dict[str, str]：家族專屬的 coverage 直方圖欄位
RNG 消耗順序（每題一個 rng）：sample() → generate.py 抽模板、姓、名 → context()。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from numerals import format_salary, int_to_cn  # noqa: E402
from sampling import allocate_quota, pick_weighted  # noqa: E402

# 月薪抽樣：粗略錨定主計總處分位數（v1 佔位；release 時換成實際分位表並記來源）
SALARY_BUCKETS = [((29500, 32000), 0.15), ((32001, 40000), 0.30), ((40001, 55000), 0.30), ((55001, 80000), 0.17), ((80001, 150000), 0.08)]
SALARY_ANCHORS = [29500, 36000]  # 最低工資、勞動部宣導範例
ANCHOR_RATE = 0.10
NON_DIVISIBLE_RATE = 0.40  # 不能被 240 整除的月薪比例下限（觸發捨入）
NUMERAL_STYLES = [("arabic", 0.45), ("arabic_plain", 0.20), ("wan", 0.20), ("cn", 0.15)]


def sample_salary(rng: random.Random) -> int:
    if rng.random() < ANCHOR_RATE:
        return rng.choice(SALARY_ANCHORS)
    r = rng.random(); acc = 0.0
    for (lo, hi), w in SALARY_BUCKETS:
        acc += w
        if r <= acc:
            s = rng.randint(lo, hi)
            if rng.random() < NON_DIVISIBLE_RATE and s % 240 == 0:
                s += rng.randint(1, 239)
            elif rng.random() >= NON_DIVISIBLE_RATE:
                s = (s // 100) * 100  # 多數人薪水是整百
            return min(s, 150000)
    return 36000


def plan(spec: dict, n: int, rng: random.Random) -> list[dict]:
    """按 quota 分配題數，邊界值優先填入。"""
    counts = allocate_quota(spec["branches"], n)
    out = []
    for b in spec["branches"]:
        lo, hi = b["hours"]
        hours_pool = list(range(lo, hi + 1))
        boundaries = [h for h in spec["boundary_values"]["hours"] if lo <= h <= hi]
        for i in range(counts[b["id"]]):
            h = boundaries[i % len(boundaries)] if i < len(boundaries) else rng.choice(hours_pool)
            out.append({"branch": b["id"], "day_type": b["day_type"], "hours": h,
                        "template_pool": "zero_templates" if h == 0 else "templates"})
    rng.shuffle(out)
    return out


def sample(rng: random.Random, planned: dict) -> dict:
    return {"monthly_salary": sample_salary(rng), "day_type": planned["day_type"], "hours": planned["hours"]}


def context(rng: random.Random, params: dict, tpl: dict) -> tuple[dict, dict]:
    numeral = pick_weighted(rng, NUMERAL_STYLES)
    day_variant = rng.randrange(len(tpl["day_phrases"][params["day_type"]]))
    fields = {
        "salary": format_salary(params["monthly_salary"], numeral),
        "day_phrase": tpl["day_phrases"][params["day_type"]][day_variant],
        "hours": params["hours"],
        "hours_cn": int_to_cn(params["hours"]),
    }
    return fields, {"numeral": numeral, "day_variant": day_variant}


def visible(params: dict, fields: dict) -> list[set[str]]:
    """題幹要能找回月薪與（非零的）時數，防模板佔位符漏填或數字格式壞掉。"""
    s = params["monthly_salary"]
    sets = [{f"{s:,}", str(s), format_salary(s, "wan"), int_to_cn(s)}]
    h = params["hours"]
    if h > 0:
        sets.append({f"{h}小時", f"{int_to_cn(h)}小時"})
    return sets


def coverage_keys(params: dict) -> dict[str, str]:
    s = params["monthly_salary"]
    bucket = next((f"{lo}-{hi}" for (lo, hi), _ in SALARY_BUCKETS if lo <= s <= hi), "other")
    return {"day_type": params["day_type"], "salary_bucket": bucket}
