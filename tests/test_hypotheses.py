import importlib.util
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
LUNA_LOG = ROOT / "eval/logs/2026Q4-superseded/2026-10-09T05-15-48-00-00_overtime-pay-2026Q4-public_9GSWZsSTujztXcBMJEqg6n.eval"


def load(path: Path, name: str):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def hyp(fid):
    return load(ROOT / "silver/families" / fid / "hypotheses.py", f"{fid}_hypotheses_under_test")


def test_overtime_hypotheses_keep_known_names_and_naive():
    h = hyp("overtime_pay")
    H = h.hypotheses({"monthly_salary": 36000, "day_type": "rest_day", "hours": 8})
    assert H["correct"] == 1900 and H["all_4/3"] == 1600 and "us_1.5x@÷176" in H and list(H)[0] == "correct"
    assert h.naive_answer({"monthly_salary": 36000, "day_type": "weekday", "hours": 4}) == 800
    assert h.naive_answer({"monthly_salary": 36000, "day_type": "holiday", "hours": 3}) == 1200
    assert h.ROUNDING_FALLBACK == 5


def test_overtime_hypotheses_follow_the_0_2_0_rule():
    """加班費 0.2.0 起：休息日第 9 小時起 ×8/3、總額無條件進位。舊規則（第 9 小時起 ×5/3、四捨五入）改列為錯答假說。"""
    h = hyp("overtime_pay")
    H = h.hypotheses({"monthly_salary": 36000, "day_type": "rest_day", "hours": 12})
    assert H["correct"] == 3500 and H["rest_day_5/3_after_8h(no_base_pay)"] == 2900
    assert "rest_day_tiers_8/3_after_8h" not in H
    H = h.hypotheses({"monthly_salary": 29500, "day_type": "holiday", "hours": 9})
    assert (H["correct"], H["rounded_half_up"], H["truncated"]) == (1148, 1147, 1147)
    assert list(H)[:3] == ["correct", "rounded_half_up", "truncated"]          # 算法對、取整錯，命中優先
    assert h.naive_answer({"monthly_salary": 29530, "day_type": "weekday", "hours": 1}) == 165   # 164.06 → 無條件進位


def test_rounding_hypotheses_match_only_exactly():
    """容許 ±1 元時，rounded_half_up 會把捨去與差 1 元的錯答都吸進來（2026-10-11 review）；取整假說只認完全相同。"""
    assert set(hyp("overtime_pay").EXACT_MATCH) == {"rounded_half_up", "truncated"}


def test_the_old_rest_day_rule_is_the_first_wrong_rule_for_hours_9_to_12():
    """0.1.x 的規則（第 9 小時起 ×5/3）是 R3 最典型的錯；只在 9 小時以上才有意義，排在其他錯規則前面。
    「做一給四」（2018 年修法前）第 9–12 小時同樣是 ×8/3。"""
    h = hyp("overtime_pay")
    H = h.hypotheses({"monthly_salary": 36000, "day_type": "rest_day", "hours": 12})
    assert list(H)[:4] == ["correct", "rounded_half_up", "truncated", "rest_day_5/3_after_8h(no_base_pay)"]
    assert H["rest_day_do1give4(old)"] == 3500                       # 12 小時本來就以 12 小時計
    assert h.hypotheses({"monthly_salary": 36000, "day_type": "rest_day", "hours": 9})["rest_day_do1give4(old)"] == 3500
    assert "rest_day_5/3_after_8h(no_base_pay)" not in h.hypotheses({"monthly_salary": 36000, "day_type": "rest_day", "hours": 8})


def test_annual_leave_hypotheses():
    h = hyp("annual_leave_days")
    p = {"hire_date": "2023-03-15", "query_date": "2024-03-14"}
    H = h.hypotheses(p)
    assert list(H)[0] == "correct" and H["correct"] == 3
    assert H["off_by_one(相當日前一日當已滿)"] == 7
    assert H["days/365(忽略§121)"] == 7          # 365 天 → 365 算法已當作滿一年，正解仍是 3
    assert hyp("annual_leave_days").hypotheses({"hire_date": "2010-01-15", "query_date": "2020-01-15"})["stuck_at_15(10年以上不加)"] == 15
    assert h.hypotheses({"hire_date": "1990-01-15", "query_date": "2020-01-15"})["no_cap(忘記30日封頂)"] == 36
    assert h.naive_answer({"hire_date": "2023-03-15", "query_date": "2024-03-14"}) == 7   # 相當日前一日就給 7（正解 3）
    assert h.naive_answer({"hire_date": "2023-09-15", "query_date": "2024-03-13"}) == 3
    assert getattr(h, "ROUNDING_FALLBACK", 0) == 0                                        # 天數題沒有「捨入誤差」這類


def test_hypothesis_modules_coexist_in_one_process():
    # 兩個 family 的 hypotheses 各自載入自己的 rule.py（不能互相覆蓋）
    ot, al = hyp("overtime_pay"), hyp("annual_leave_days")
    assert ot.hypotheses({"monthly_salary": 36000, "day_type": "weekday", "hours": 2})["correct"] == 400
    assert al.hypotheses({"hire_date": "2020-01-15", "query_date": "2021-01-15"})["correct"] == 7


@pytest.mark.skipif(not LUNA_LOG.exists(), reason="baseline log not present")
def test_attribution_unchanged_on_committed_overtime_log():
    ae = load(ROOT / "eval/attribute_errors.py", "attribute_errors_under_test")
    sm = ae.attribute(LUNA_LOG)["summary"]
    assert (sm["wrong_scored"], sm["n_scored"]) == (55, 190)
    # 0.2.0起：當時判錯、其實照勞動部 ×8/3 與無條件進位作答的歸 correct_under_current_rule；
    # 取整假說（rounded_half_up、truncated）只認完全相同；捨入誤差跟現行正解比（差 5 元內）。
    assert sm["by_hypothesis"] == {"unexplained": 14, "correct_under_current_rule": 12, "rounding(|Δ|≤5)": 6, "truncated": 5,
                                   "holiday_daily_rounded+tiers": 4, "rounded_half_up": 4, "double(2x)": 3,
                                   "holiday_hourly_1x(w*h)": 2, "rest_day_do1give4(old)": 2, "all_5/3": 1, "all_4/3": 1,
                                   "tiers,hourly_rounded_first": 1}
    assert sm["by_branch"]["R3"] == {"correct_under_current_rule": 11, "rounded_half_up": 4, "rounding(|Δ|≤5)": 3, "unexplained": 2}


@pytest.mark.skipif(not LUNA_LOG.exists(), reason="baseline log not present")
def test_old_log_answers_that_follow_the_current_rule_are_named_so():
    """這份 log 是用 0.1.x 的 gold 評分的。R3 被判錯的 20 題，多數其實是照勞動部的 ×8/3 作答：
    與現行正解完全相同的歸 correct_under_current_rule（11），×8/3 但四捨五入的歸 rounded_half_up（4），
    與現行正解差 5 元內的歸捨入誤差（3）；只有 2 題無法解釋。"""
    ae = load(ROOT / "eval/attribute_errors.py", "attribute_errors_current_rule")
    r3 = ae.attribute(LUNA_LOG)["summary"]["by_branch"]["R3"]
    assert r3 == {"correct_under_current_rule": 11, "rounded_half_up": 4, "rounding(|Δ|≤5)": 3, "unexplained": 2}


def test_attribution_tol_is_per_family():
    ae = load(ROOT / "eval/attribute_errors.py", "attribute_errors_tol_under_test")
    assert ae.attribution_tol("overtime_pay") == 1          # 元：吸收捨入差
    assert ae.attribution_tol("annual_leave_days") == 0     # 天數：相鄰假說只差 1 日，容許 ±1 會把錯答抹到多個假說


def test_attribute_uses_the_family_tolerance_by_default():
    import glob
    from inspect_ai.log import read_eval_log
    if not (ROOT / "eval/logs/2026Q4").is_dir():
        pytest.skip("公開版不帶原始評測紀錄；歸因結果在逐題得分表 eval/scores/ 的 attribution 欄")
    ae = load(ROOT / "eval/attribute_errors.py", "attribute_errors_tol2_under_test")
    logs = [f for f in sorted(glob.glob(str(ROOT / "eval/logs/2026Q4/*annual-leave*.eval")))
            if read_eval_log(f, header_only=True).eval.metadata.get("evaluation_target") == "fake-naive@none@none@direct"]
    assert logs, "annual_leave fake-naive log missing"
    assert ae.attribute(Path(logs[-1]))["summary"]["tol"] == 0
    if LUNA_LOG.exists():     # 封存的舊 log；公開 repo 不含封存目錄時只檢查特休這一半
        assert ae.attribute(LUNA_LOG)["summary"]["tol"] == 1
