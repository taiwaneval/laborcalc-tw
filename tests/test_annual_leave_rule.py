import importlib.util
from datetime import date
from pathlib import Path
import pytest
import yaml
ROOT = Path(__file__).resolve().parents[1]
FAM = ROOT / "silver/families/annual_leave_days"
_s = importlib.util.spec_from_file_location("annual_leave_days_rule_under_test", FAM / "rule.py")
rule = importlib.util.module_from_spec(_s); _s.loader.exec_module(rule)


def test_entitlement_date_normal_and_month_end():
    assert rule.entitlement_date(date(2023, 3, 15), 12) == date(2024, 3, 15)
    assert rule.entitlement_date(date(2024, 2, 29), 12) == date(2025, 3, 1)      # 無相當日 → 末日翌日
    assert rule.entitlement_date(date(2023, 8, 31), 6) == date(2024, 3, 1)       # 2024-02 無 31 日
    assert rule.entitlement_date(date(2023, 1, 31), 6) == date(2023, 7, 31)      # 有相當日
    assert rule.entitlement_date(date(2023, 1, 31), 1) == date(2023, 3, 1)       # 2023-02 無 31 日


def test_completed_months_boundaries():
    assert rule.completed_months(date(2023, 3, 15), date(2024, 3, 14)) == 11
    assert rule.completed_months(date(2023, 3, 15), date(2024, 3, 15)) == 12
    assert rule.completed_months(date(2023, 3, 15), date(2023, 3, 15)) == 0


def test_feb29_hire():
    assert rule.compute("2024-02-29", "2025-02-28")["total"] == 3
    assert rule.compute("2024-02-29", "2025-03-01")["total"] == 7


def test_day31_hire_short_month():
    assert rule.compute("2023-08-31", "2024-02-29")["total"] == 0
    assert rule.compute("2023-08-31", "2024-03-01")["total"] == 3


def test_same_day_and_before_hire():
    assert rule.compute("2020-01-15", "2020-01-15")["total"] == 0
    with pytest.raises(ValueError):
        rule.compute("2020-01-15", "2020-01-14")


def test_cap_and_linear():
    assert rule.compute("2010-01-15", "2020-01-15")["total"] == 16   # 10 年
    assert rule.compute("1997-01-15", "2020-01-15")["total"] == 29   # 23 年
    assert rule.compute("1996-01-15", "2020-01-15")["total"] == 30   # 24 年
    assert rule.compute("1990-01-15", "2020-01-15")["total"] == 30   # 30 年


def test_tiers_table():
    assert [rule.days_for_months(m) for m in (0, 5, 6, 11, 12, 23, 24, 35, 36, 59, 60, 119, 120, 132, 287, 288)] == \
           [0, 0, 3, 3, 7, 7, 10, 10, 14, 14, 15, 15, 16, 17, 29, 30]


def test_tier_labels_and_exact_value():
    r = rule.compute("2023-03-15", "2024-03-15")
    assert (r["tier"], r["total"], r["total_exact"], r["completed_months"], r["completed_years"]) == ("1y", 7, 7, 12, 1)
    assert rule.compute("2020-01-15", "2020-03-01")["tier"] == "lt6m"
    assert rule.compute("2010-01-15", "2020-01-15")["tier"] == "10y+"
    assert rule.compute("1990-01-15", "2020-01-15")["tier"] == "cap"


def test_golden_cases_from_spec():
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    for g in spec["golden_cases"]:
        assert rule.compute(**g["input"])["total"] == g["expected"], g["id"]


# ---- 無效日期：兩份實作都必須拒絕，且行為一致
import json  # noqa: E402
import subprocess  # noqa: E402

INVALID = ["2023-02-30", "2023-02-29", "2023-04-31", "2023-13-01", "2023-00-10", "2023-01-00",
           "20231015", "2023-1-5", "2023-01-05T00:00"]
VALID_EDGES = ["2024-02-29", "2023-01-31", "2020-12-31", "2026-06-30"]
QUERY = "2026-06-30"


def _js_totals(hires: list[str]) -> list:
    script = ("const R=require(process.argv[1]);const xs=JSON.parse(process.argv[2]);"
              "console.log(JSON.stringify(xs.map(x=>{try{return R.compute(x,'" + QUERY + "').total}catch(e){return 'ERR'}})));")
    out = subprocess.run(["node", "-e", script, str(FAM / "rule.js"), json.dumps(hires)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_js_rejects_nonexistent_and_malformed_dates():
    assert dict(zip(INVALID, _js_totals(INVALID))) == {x: "ERR" for x in INVALID}


@pytest.mark.parametrize("bad", INVALID)
def test_python_rejects_nonexistent_and_malformed_dates(bad):
    with pytest.raises(ValueError):
        rule.compute(bad, QUERY)


def test_both_implementations_agree_on_valid_edge_dates():
    assert _js_totals(VALID_EDGES) == [rule.compute(x, QUERY)["total"] for x in VALID_EDGES]
