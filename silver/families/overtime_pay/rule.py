"""overtime_pay — Python 實作（pipeline 用的驗證器）。

法源與適用範圍見 spec.yaml。此檔與 rule.js 為獨立實作，
test_agreement.py 以 golden cases 與隨機輸入檢查兩者一致。
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import TypedDict

DAY_TYPES = ("weekday", "rest_day", "holiday")
MAX_HOURS = {"weekday": 4, "rest_day": 12, "holiday": 12}  # LSA-32-2
LABEL = {"weekday": "平日延長工時", "rest_day": "休息日出勤", "holiday": "國定假日出勤"}

R_FIRST_2H = Fraction(4, 3)  # LSA-24-1 第 1 款／LSA-24-2 前段
R_AFTER_2H = Fraction(5, 3)  # LSA-24-1 第 2 款／LSA-24-2 後段
R_REST_AFTER_8H = Fraction(8, 3)  # MOL-RESTDAY-9H：休息日第 9 小時起 2⅔ 倍（勞動部加班費示意圖、試算系統）


class Item(TypedDict):
    label: str
    hours: int
    amount: Fraction


def validate(monthly: int, day_type: str, hours: int, minimum_wage: int | None = None) -> list[str]:
    errs: list[str] = []
    if not isinstance(monthly, int) or monthly <= 0:
        errs.append("月薪須為正整數")
    elif minimum_wage is not None and monthly < minimum_wage:
        errs.append(f"月薪低於最低工資 {minimum_wage}")
    if day_type not in DAY_TYPES:
        errs.append("日別不合法")
    if not isinstance(hours, int) or hours < 0:
        errs.append("時數須為非負整數")
    elif day_type in MAX_HOURS and hours > MAX_HOURS[day_type]:
        errs.append(f"{LABEL[day_type]}時數上限 {MAX_HOURS[day_type]} 小時")
    return errs


def hourly_rate(monthly: int) -> Fraction:
    """MOL-HOURLY：平日每小時工資額 = 月薪 ÷ 30 ÷ 8。不先捨入。"""
    return Fraction(monthly, 30 * 8)


def round_up(x: Fraction) -> int:
    """rounding.final：無條件進位到元。加給「以上」的法定下限是精確金額，取整不能低於它（勞動部試算系統同）。"""
    return math.ceil(x)


def compute(monthly_salary: int, day_type: str, hours: int) -> dict:
    monthly = monthly_salary
    errs = validate(monthly, day_type, hours)
    if errs:
        raise ValueError("; ".join(errs))

    w = hourly_rate(monthly)
    items: list[Item] = []

    if day_type == "weekday":
        h1 = min(hours, 2)
        h2 = max(hours - 2, 0)
        if h1:
            items.append({"label": "前 2 小時 ×4/3", "hours": h1, "amount": w * R_FIRST_2H * h1})
        if h2:
            items.append({"label": "第 3 小時起 ×5/3", "hours": h2, "amount": w * R_AFTER_2H * h2})
    elif day_type == "rest_day":  # LSA-24-2、MOL-RESTDAY-9H
        h1 = min(hours, 2)
        h2 = min(max(hours - 2, 0), 6)
        h3 = max(hours - 8, 0)
        if h1:
            items.append({"label": "前 2 小時 ×4/3", "hours": h1, "amount": w * R_FIRST_2H * h1})
        if h2:
            items.append({"label": "第 3–8 小時 ×5/3", "hours": h2, "amount": w * R_AFTER_2H * h2})
        if h3:
            items.append({"label": "第 9 小時起 ×8/3", "hours": h3, "amount": w * R_REST_AFTER_8H * h3})
    else:  # holiday — LSA-39
        if hours > 0:
            items.append({"label": "8 小時內加發一日工資", "hours": min(hours, 8), "amount": Fraction(monthly, 30)})
        over = max(hours - 8, 0)
        h1 = min(over, 2)
        h2 = max(over - 2, 0)
        if h1:
            items.append({"label": "第 9–10 小時 ×4/3", "hours": h1, "amount": w * R_FIRST_2H * h1})
        if h2:
            items.append({"label": "第 11–12 小時 ×5/3", "hours": h2, "amount": w * R_AFTER_2H * h2})

    total_exact = sum((i["amount"] for i in items), Fraction(0))
    return {
        "hourly": w,
        "items": items,
        "total_exact": total_exact,
        "total": round_up(total_exact),
    }


if __name__ == "__main__":
    import json
    import sys

    # stdin: JSON list of {monthly_salary, day_type, hours} → stdout: JSON list of totals
    cases = json.load(sys.stdin)
    out = [compute(c["monthly_salary"], c["day_type"], c["hours"])["total"] for c in cases]
    json.dump(out, sys.stdout)
