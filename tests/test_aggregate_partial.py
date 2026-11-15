import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("aggregate_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)

FAMS = ["annual_leave_days", "overtime_pay"]
W = {"annual_leave_days": 1.0, "overtime_pay": 1.0}


def fam(acc, complete=True, n_ctl=10, ok_ctl=10):
    return {"tier": 1, "complete": complete, "accuracy": acc, "n_control": n_ctl, "correct_control": ok_ctl}


def test_summarize_split_marks_unavailable_but_keeps_family_scores():
    out = agg.summarize_split({"overtime_pay": fam(0.71)}, families=FAMS, weights=W)
    assert out["status"] == "unavailable" and out["score"] is None
    assert out["reason"]["missing_families"] == ["annual_leave_days"]
    assert out["family_scores"] == {"annual_leave_days": None, "overtime_pay": 0.71}


def test_summarize_split_ok_when_all_headline_families_complete():
    out = agg.summarize_split({"overtime_pay": fam(0.4), "annual_leave_days": fam(0.8, ok_ctl=5)}, families=FAMS, weights=W)
    assert out["status"] == "ok" and abs(out["score"] - 0.6) < 1e-12
    assert out["family_scores"] == {"annual_leave_days": 0.8, "overtime_pay": 0.4}
    assert out["control_pass"] == 0.75 and out["n_control"] == 20


def test_incomplete_family_is_unavailable_with_its_partial_score_shown():
    out = agg.summarize_split({"overtime_pay": fam(0.4), "annual_leave_days": fam(0.8, complete=False)}, families=FAMS, weights=W)
    assert out["status"] == "unavailable" and out["reason"]["incomplete_families"] == ["annual_leave_days"]
    assert out["family_scores"]["annual_leave_days"] == 0.8


def test_summary_line_shows_partial_scores_for_unavailable_targets():
    out = agg.summarize_split({"overtime_pay": fam(0.71)}, families=FAMS, weights=W)
    line = agg.summary_line("gpt-6-luna@api@opencode@direct", "public", out)
    assert "unavailable" in line and "partial: overtime_pay=0.71" in line and "annual_leave_days" not in line.split("partial:")[1]


def test_summary_line_marks_a_family_whose_run_is_incomplete():
    """部分分數裡，只跑了一部分題目的 family 要標 incomplete 與題數，不能看起來和跑完的一樣。"""
    fams = {"overtime_pay": fam(0.4), "annual_leave_days": {**fam(0.8, complete=False), "n": 150, "expected_n": 200}}
    line = agg.summary_line("m@q@h@direct", "public", agg.summarize_split(fams, families=FAMS, weights=W))
    assert "annual_leave_days=0.80 (incomplete 150/200)" in line
    assert "overtime_pay=0.40" in line and "overtime_pay=0.40 (incomplete" not in line
