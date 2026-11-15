"""overtime_pay 的錯規則假說與 naive 假 solver（從 eval/attribute_errors.py 搬出，假說內容逐字不變）。

供 eval/attribute_errors.py（錯答歸因）與 eval/tasks/rules_registry.py（naive 假 solver）依 family 載入。
介面：hypotheses(params) -> dict[str, int]（第一鍵 "correct"，dict 順序 = 命中優先序）；naive_answer(params) -> int；
ROUNDING_FALLBACK：錯答與正解相差在此元數內、又不符合任何假說時，歸為「捨入誤差」。
0.2.0：正解改為休息日第 9 小時起 ×8/3、總額無條件進位；假說值也依題目說明無條件進位。
舊規則（第 9 小時起 ×5/3）改列為錯答假說；「算法對、取整錯」的 rounded_half_up／truncated 緊接在 correct 之後。
"""
from __future__ import annotations

import importlib.util
import math
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """以唯一模組名載入同目錄的 .py（不用裸名稱 import，避免兩個 family 的 rule.py 互相覆蓋）。"""
    s = importlib.util.spec_from_file_location(f"{HERE.name}_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


rule = _load_sibling("rule")

F = Fraction
ROUNDING_FALLBACK = 5   # 元
ATTRIBUTION_TOL = 1   # 元：吸收捨入差
# 取整錯誤只認完全相同：容許 ±1 會把捨去與差 1 元的錯答都標成「四捨五入」。小數部分 < 0.5 時兩者同值，歸 rounded_half_up。
EXACT_MATCH = ("rounded_half_up", "truncated")


def rhu(x: Fraction) -> int:
    return int(x + F(1, 2))


def tiers(w: Fraction, h: int, r1=F(4, 3), r2=F(5, 3)) -> Fraction:
    """前 2 小時 r1、第 3 小時起 r2。"""
    return w * r1 * min(h, 2) + w * r2 * max(h - 2, 0)


def rest_tiers(w: Fraction, h: int) -> Fraction:
    """休息日的正確分段：前 2 小時 ×4/3、第 3–8 小時 ×5/3、第 9 小時起 ×8/3（MOL-RESTDAY-9H）。"""
    return tiers(w, min(h, 8)) + w * F(8, 3) * max(h - 8, 0)


def hypotheses(params: dict) -> dict[str, int]:
    """假說名 → 該假說算出的整數（依題目說明無條件進位）。順序 = 命中時的優先順序（愈前愈「典型」）；第一個鍵是 "correct"。"""
    monthly, day_type, hours = params["monthly_salary"], params["day_type"], params["hours"]
    w240 = F(monthly, 240)   # 月薪 ÷ 30 ÷ 8（正確）
    w176 = F(monthly, 176)   # 月薪 ÷ 22 ÷ 8（常見錯：用工作日數）
    w160 = F(monthly, 160)   # 月薪 ÷ 20 ÷ 8
    daily = F(monthly, 30)
    H: dict[str, Fraction] = {}

    exact = rule.compute(monthly, day_type, hours)["total_exact"]
    H["correct"] = exact
    if day_type == "rest_day" and hours > 8:
        H["rest_day_5/3_after_8h(no_base_pay)"] = tiers(w240, hours)   # 0.1.x 的規則：第 9 小時起漏給原本工資；R3 最典型的錯

    # 「外國規則」：cot 回覆常見的美式 1.5 倍（overtime = 1.5×）、休息日／假日 2 倍、逾 8 小時 3 倍，
    # 時薪基礎用 ÷176（22 天×8）、÷220、÷240。這組是 2026-10-08 gemma-4-e4b cot run 讀回覆歸納的。
    w220 = F(monthly, 220)
    for bname, wb in (("÷176", w176), ("÷220", w220), ("÷240", w240), ("÷160", w160)):
        H[f"us_1.5x@{bname}"] = wb * F(3, 2) * hours
        H[f"us_0.5x_premium_only@{bname}"] = wb * F(1, 2) * hours          # 只給「另行給付」的 0.5 倍加成
        H[f"2x@{bname}"] = wb * 2 * hours
        H[f"1x@{bname}"] = wb * hours
        H[f"2x_to_8h_then_3x@{bname}"] = wb * 2 * min(hours, 8) + wb * 3 * max(hours - 8, 0)
        H[f"1.5x_to_8h_then_2x@{bname}"] = wb * F(3, 2) * min(hours, 8) + wb * 2 * max(hours - 8, 0)

    if day_type in ("weekday", "rest_day"):
        H["premium_only(1/3,2/3)"] = w240 * F(1, 3) * min(hours, 2) + w240 * F(2, 3) * max(hours - 2, 0)  # 只給加成部分
        H["all_4/3"] = w240 * F(4, 3) * hours                       # 全程 4/3（忘了 5/3）
        H["all_5/3"] = w240 * F(5, 3) * hours
        H["no_premium(1x)"] = w240 * hours                          # 無加成
        H["double(2x)"] = w240 * 2 * hours
        H["tiers@÷176"] = tiers(w176, hours)
        H["all_4/3@÷176"] = w176 * F(4, 3) * hours
        H["tiers@÷160"] = tiers(w160, hours)
        H["tiers,hourly_rounded_first"] = tiers(F(rhu(w240)), hours)
        if day_type == "rest_day":
            # 2018 修法前「做一給四」：1–4h 以 4h 計、5–8h 以 8h 計、9–12h 以 12h 計
            h4 = 4 if hours <= 4 else 8 if hours <= 8 else 12
            H["rest_day_do1give4(old)"] = rest_tiers(w240, h4)
            H["rest_day_as_holiday(daily_wage)"] = daily       # 把休息日當國定假日：加發一日工資
            H["rest_day_daily+tiers"] = daily + tiers(w240, hours)
        else:
            H["weekday_daily_wage"] = daily                       # 平日加班也給一日工資
            H["weekday_daily_prorated(h/8)"] = daily * F(hours, 8)
    else:  # holiday
        H["holiday_tiers_as_weekday"] = tiers(w240, hours)       # 當平日加班算，忘了「加發一日工資」
        H["holiday_hourly_1x(w*h)"] = w240 * hours               # 只給出勤時數的工資
        H["holiday_double(2*w*h)"] = w240 * 2 * hours            # 「雙倍」整日
        H["holiday_daily_only(忽略>8h)"] = daily                 # 一日工資，逾 8 小時部分漏算
        H["holiday_daily+1x_over8"] = daily + w240 * max(hours - 8, 0)
        H["holiday_daily+1x_all_hours"] = daily + w240 * hours
        H["holiday_daily+tiers_all_hours"] = daily + tiers(w240, hours)
        H["holiday_2*daily"] = 2 * daily
        H["holiday_daily_prorated(h/8)"] = daily * F(min(hours, 8), 8)
        H["holiday_tiers@÷176"] = tiers(w176, hours)
        H["holiday_daily_rounded+tiers"] = F(rhu(daily)) + tiers(F(rhu(w240)), max(hours - 8, 0))
    H["zero"] = F(0)
    # 優先序：台灣規則的變形（上面依 day_type 加的）先於外國規則；dict 保留插入順序，這裡把 us_/2x_ 系列移到最後
    foreign = {k: H.pop(k) for k in list(H) if k.startswith(("us_", "2x", "1x@", "1.5x_", "2x_"))}
    H.update(foreign)
    out = {k: math.ceil(v) for k, v in H.items()}
    # 算法對、取整錯（0.2.0 前的題目說明是四捨五入）：插在 correct 之後，命中優先
    rounding = {"rounded_half_up": rhu(exact), "truncated": math.floor(exact)}
    return {"correct": out.pop("correct"), **rounding, **out}


def naive_answer(params: dict) -> int:
    """模擬「只記得 4/3、忘了 5/3 與國定假日規則」的弱模型：平日／休息日全用 ×4/3，國定假日只給一日工資。
    依題目說明無條件進位（0.2.0）；否則 W1 這類它本該答對的分支，也會因取整不同而被判錯。"""
    m, h = params["monthly_salary"], params["hours"]
    if params["day_type"] == "holiday":
        ans = F(m, 30) if h > 0 else F(0)
    else:
        ans = F(m, 240) * F(4, 3) * h
    return math.ceil(ans)
