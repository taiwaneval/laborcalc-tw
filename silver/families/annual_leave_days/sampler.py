"""annual_leave_days 抽樣器：先抽查詢日，再依 branch 反推到職日；日期呈現（民國／西元）為旋鈕。

契約見 silver/families/overtime_pay/sampler.py 檔頭。抽樣範圍一律讀 spec.parameters（ranges()），由 plan() 帶進每個 planned dict。
分支：
  tier                  已滿月數在 branch.months 區間內（先抽月數，再往前推 0–27 日，以 rule 驗證）
  boundary on           查詢日 = 到職日加 k 個月之相當日當天（新階梯）
  boundary before       查詢日 = 相當日前一日（舊階梯）
  no_corresponding_day  到職日 29／30／31，滿期之月無相當日（民法 §121 但書）；查詢日為該月末日或翌日。
                        這類日期很稀少，以列舉候選組合的方式抽（_b3_candidates），不做拒絕抽樣。
"""
from __future__ import annotations

import calendar
import importlib.util
import random
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
from sampling import allocate_quota, pick_weighted  # noqa: E402


def _load_sibling(name: str):
    """以唯一模組名載入同目錄的 .py。不用裸名稱 `import rule`：兩個 family 的 rule.py 在同一 process 會互相覆蓋。"""
    s = importlib.util.spec_from_file_location(f"{HERE.name}_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


rule = _load_sibling("rule")

DATE_STYLES = [("roc_full", 0.30), ("roc_dot", 0.15), ("ce_full", 0.35), ("ce_slash", 0.20)]
MAX_ATTEMPTS = 200


def format_date(d: date, style: str) -> str:
    if style == "roc_full":
        return f"民國{d.year - 1911}年{d.month}月{d.day}日"
    if style == "roc_dot":
        return f"{d.year - 1911}.{d.month}.{d.day}"
    if style == "ce_full":
        return f"{d.year}年{d.month}月{d.day}日"
    if style == "ce_slash":
        return f"{d.year}/{d.month}/{d.day}"
    raise ValueError(style)


def ranges(spec: dict) -> dict:
    """抽樣範圍（spec.parameters），以可 hash 的純值帶進 planned dict。"""
    q = spec["parameters"]["query_date"]
    years = spec["parameters"]["hire_date"]["max_years_before_query"]
    return {"q_min": q["min"], "q_max": q["max"], "max_back_days": years * 366}


def _shift_months(d: date, delta: int) -> date:
    """加減月數；目標月沒有該日時取月末（只用於反推到職日，之後一律以 rule 驗證）。"""
    y, m0 = divmod(d.year * 12 + (d.month - 1) + delta, 12)
    return date(y, m0 + 1, min(d.day, calendar.monthrange(y, m0 + 1)[1]))


def _rand_date(rng: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def _params(h: date, q: date) -> dict:
    return {"hire_date": h.isoformat(), "query_date": q.isoformat()}


@lru_cache(maxsize=None)
def _b3_candidates(k: int, q_min: str, q_max: str, max_back_days: int) -> tuple[tuple[date, date], ...]:
    """所有 (到職日, 查詢日)：到職日 29–31 日、加 k 個月的那個月沒有這一天，查詢日為該月末日或翌日，且在範圍內。"""
    lo, hi = date.fromisoformat(q_min), date.fromisoformat(q_max)
    out = []
    for y in range(lo.year - 1, hi.year + 1):
        for mo in range(1, 13):
            last = calendar.monthrange(y, mo)[1]
            for d in (29, 30, 31):
                if d <= last:
                    continue                      # 目標月有相當日，不是 B3
                hy, hm0 = divmod(y * 12 + (mo - 1) - k, 12)
                if calendar.monthrange(hy, hm0 + 1)[1] < d:
                    continue                      # 到職月本身沒有這一天
                h = date(hy, hm0 + 1, d)
                e = rule.entitlement_date(h, k)   # 目標月末日之翌日
                for q in (e - timedelta(days=1), e):
                    if lo <= q <= hi and (q - h).days <= max_back_days:
                        out.append((h, q))
    if not out:
        raise RuntimeError(f"B3：錨 {k} 個月在查詢日範圍內沒有缺相當日的組合（spec.boundary_values.b3_tier_months 不應含此值）")
    return tuple(out)


def plan(spec: dict, n: int, rng: random.Random) -> list[dict]:
    """B1／B2 的錨（滿幾個月）輪流指派，每個錨至少出現 ⌊n_branch / 錨數⌋ 次；
    B3 的錨依候選組合數加權抽（候選多的 6 個月為主，2/29 到職的少數組合不會被硬塞重複，也降低公開／私有題撞題）。
    錨記在 knobs.anchor_months，coverage 會列出直方圖。"""
    counts = allocate_quota(spec["branches"], n)
    rg, bv = ranges(spec), spec["boundary_values"]
    b3_anchors = bv["b3_tier_months"]
    b3_weights = [len(_b3_candidates(k, rg["q_min"], rg["q_max"], rg["max_back_days"])) for k in b3_anchors]
    out = []
    for b in spec["branches"]:
        for i in range(counts[b["id"]]):
            p = {"branch": b["id"], "kind": b["kind"], "template_pool": b.get("template_pool", "templates"), **rg}
            if b["kind"] == "tier":
                p["months"] = b["months"]
            else:
                p["boundary"] = b["boundary"]
                if b["boundary"] == "no_corresponding_day":
                    k = rng.choices(b3_anchors, weights=b3_weights)[0]
                else:
                    k = bv["tier_months"][i % len(bv["tier_months"])]
                p["tier_months"] = k
                p["knobs"] = {"anchor_months": k}
            out.append(p)
    rng.shuffle(out)
    return out


def sample(rng: random.Random, planned: dict) -> dict:
    """先抽查詢日 q，再依 branch 反推到職日 h。只用傳入的 rng，確定性。"""
    if planned["kind"] == "boundary" and planned["boundary"] == "no_corresponding_day":
        h, q = rng.choice(_b3_candidates(planned["tier_months"], planned["q_min"], planned["q_max"], planned["max_back_days"]))
        return _params(h, q)
    q_min, q_max = date.fromisoformat(planned["q_min"]), date.fromisoformat(planned["q_max"])
    for _ in range(MAX_ATTEMPTS):
        q = _rand_date(rng, q_min, q_max)
        if planned["kind"] == "tier":
            lo, hi = planned["months"]
            h = _shift_months(q, -rng.randint(lo, hi)) - timedelta(days=rng.randint(0, 27))
            ok = lo <= rule.completed_months(h, q) <= hi
        elif planned["boundary"] == "on":
            k = planned["tier_months"]
            h = _shift_months(q, -k)
            ok = rule.entitlement_date(h, k) == q
        elif planned["boundary"] == "before":
            k = planned["tier_months"]
            e = q + timedelta(days=1)
            h = _shift_months(e, -k)
            ok = rule.entitlement_date(h, k) == e
        else:
            raise ValueError(f"未知的 boundary：{planned['boundary']!r}")
        if ok and (q - h).days <= planned["max_back_days"]:
            return _params(h, q)
    raise RuntimeError(f"{MAX_ATTEMPTS} 次都抽不到符合 branch 的日期：{planned}")


def context(rng: random.Random, params: dict, tpl: dict) -> tuple[dict, dict]:
    hs, qs = pick_weighted(rng, DATE_STYLES), pick_weighted(rng, DATE_STYLES)
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    return {"hire": format_date(h, hs), "query": format_date(q, qs)}, {"hire_style": hs, "query_style": qs}


def visible(params: dict, fields: dict) -> list[set[str]]:
    """題幹要能找回到職日與查詢日（任一種寫法）。"""
    return [{format_date(date.fromisoformat(params[k]), s) for s, _ in DATE_STYLES} for k in ("hire_date", "query_date")]


def coverage_keys(params: dict) -> dict[str, str]:
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    return {"days": str(rule.days_for_months(rule.completed_months(h, q)))}
