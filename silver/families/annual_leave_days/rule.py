"""annual_leave_days — Python 實作（pipeline 用的驗證器）。

法源與適用範圍見 spec.yaml。此檔與 rule.js 為獨立實作，test_agreement.py 以 golden cases 與隨機輸入檢查兩者一致。
滿期日依民法 §121：到職日加 m 個月之相當日當天起算已滿；最後之月無相當日者，以該月末日為期間末日，翌日起算已滿。
起算點依勞基法施行細則第 5 條「自受僱當日起算」（到職日算入）。無相當日的處理與勞動部特別休假日數試算系統的年資算法一致，
2092 組日期零差異（2026-10-10，docs/verification/2026-10-10-mol-annual-leave/）。
日期只接受 YYYY-MM-DD 且必須存在；與 rule.js 行為一致（不存在的日期、其他格式都報錯）。
只用標準庫。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

TIER_LABEL = {0: "lt6m", 3: "6m", 7: "1y", 10: "2y", 14: "3y", 15: "5y", 30: "cap"}


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse(d: str) -> date:
    # date.fromisoformat 在 3.11+ 也接受 20231015 等寫法；rule.js 只接受 YYYY-MM-DD，這裡先擋格式，兩邊一致
    if not isinstance(d, str) or not _ISO_DATE.match(d):
        raise ValueError(f"日期格式須為 YYYY-MM-DD: {d!r}")
    return date.fromisoformat(d)          # 不存在的日期（2023-02-30）由 fromisoformat 報 ValueError


def entitlement_date(hire: date, months: int) -> date:
    """到職日加 months 個月後「已滿」的第一天（民法 §121）。"""
    total = hire.month - 1 + months
    y, m = hire.year + total // 12, total % 12 + 1
    last = calendar.monthrange(y, m)[1]
    if hire.day <= last:
        return date(y, m, hire.day)
    return date(y, m, last) + timedelta(days=1)


def completed_months(hire: date, query: date) -> int:
    """查詢日當天已滿的月數：最大的 m 使 entitlement_date(hire, m) ≤ query。"""
    if query < hire:
        raise ValueError("查詢日早於到職日")
    m = (query.year - hire.year) * 12 + (query.month - hire.month) + 1   # 一定 > 答案：落在查詢月的下一個月
    while m > 0 and entitlement_date(hire, m) > query:
        m -= 1
    return m


def days_for_months(months: int) -> int:
    """§38 第 1 項階梯。"""
    if months < 6:
        return 0
    if months < 12:
        return 3
    years = months // 12
    if years == 1:
        return 7
    if years == 2:
        return 10
    if years < 5:
        return 14
    if years < 10:
        return 15
    return min(15 + (years - 9), 30)


def compute(hire_date: str, query_date: str) -> dict:
    hire, query = parse(hire_date), parse(query_date)
    months = completed_months(hire, query)
    days = days_for_months(months)
    return {
        "completed_months": months,
        "completed_years": months // 12,
        "tier": TIER_LABEL.get(days, "10y+"),
        "total": days,
        "total_exact": days,
    }


if __name__ == "__main__":
    import json
    import sys

    # stdin: JSON list of {hire_date, query_date} → stdout: JSON list of totals
    cases = json.load(sys.stdin)
    json.dump([compute(c["hire_date"], c["query_date"])["total"] for c in cases], sys.stdout)
