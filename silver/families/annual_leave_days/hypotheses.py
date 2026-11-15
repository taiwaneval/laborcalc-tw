"""annual_leave_days 的錯規則假說與 naive 假 solver。

供 eval/attribute_errors.py（錯答歸因）與 eval/tasks/rules_registry.py（naive 假 solver）依 family 載入。
介面：hypotheses(params) -> dict[str, int]（第一鍵 "correct"，dict 順序 = 命中優先序）；naive_answer(params) -> int。
天數題不設 ROUNDING_FALLBACK（差幾天不是捨入問題）。
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """以唯一模組名載入同目錄的 .py（不用裸名稱 import，避免兩個 family 的 rule.py 互相覆蓋）。"""
    s = importlib.util.spec_from_file_location(f"{HERE.name}_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


rule = _load_sibling("rule")
ATTRIBUTION_TOL = 0   # 天數：相鄰假說只差 1 日，容許 ±1 會把錯答抹到多個假說


def _months_by_365(h: date, q: date) -> int:
    """(Q − H) 天數換算：每 365 天算一年，餘數每 30 天算一個月（忽略閏年與民法 §121）。"""
    d = (q - h).days
    return (d // 365) * 12 + (d % 365) // 30


def _no_cap(months: int) -> int:
    years = months // 12
    return 15 + (years - 9) if years >= 10 else rule.days_for_months(months)


def hypotheses(params: dict) -> dict[str, int]:
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    m = rule.completed_months(h, q)
    H = {"correct": rule.days_for_months(m)}
    H["days/365(忽略§121)"] = rule.days_for_months(_months_by_365(h, q))
    H["off_by_one(相當日前一日當已滿)"] = rule.days_for_months(rule.completed_months(h, q + timedelta(days=1)))
    H["off_by_one(相當日當天仍未滿)"] = rule.days_for_months(rule.completed_months(h, q - timedelta(days=1))) if q > h else 0
    H["calendar_year(只看年份差)"] = rule.days_for_months((q.year - h.year) * 12)
    H["stuck_at_15(10年以上不加)"] = min(rule.days_for_months(m), 15)
    H["no_cap(忘記30日封頂)"] = _no_cap(m)
    H["skip_6m(未滿一年給0)"] = 0 if m < 12 else rule.days_for_months(m)
    H["linear_from_year1(每年加一日)"] = (m // 12 + 6) if m >= 12 else rule.days_for_months(m)
    H["zero"] = 0
    return H


def naive_answer(params: dict) -> int:
    """模擬「用 (Q − H) ÷ 365 算年資、忽略民法 §121」的弱模型：錯在滿期日附近（主要是 B2、B3）。"""
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    return rule.days_for_months(_months_by_365(h, q))
