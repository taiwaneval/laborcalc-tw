"""tools/apply_review.py：人工複核寫回 triage.yaml。再次覆寫時不能弄丟最初的 LLM 分類。"""
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import paths  # noqa: E402

_s = importlib.util.spec_from_file_location("apply_review_under_test", ROOT / "tools/apply_review.py")
ar = importlib.util.module_from_spec(_s); _s.loader.exec_module(ar)

UID = "N0030001/40/1"


def run(tmp_path, monkeypatch, unit: dict, decision: dict) -> dict:
    d = tmp_path / "inventory" / "N0030001"; d.mkdir(parents=True)
    (d / "triage.yaml").write_text(yaml.safe_dump({"triage_version": "t", "units": [unit]}, allow_unicode=True, sort_keys=False))
    rv = tmp_path / "review.yaml"
    rv.write_text(yaml.safe_dump({"reviewer": "Calvin Ku", "date": "2026-10-10", "decisions": [decision]}, allow_unicode=True))
    monkeypatch.setattr(paths, "INVENTORY", tmp_path / "inventory")
    ar.main(["N0030001", str(rv)])
    return yaml.safe_load((d / "triage.yaml").read_text())["units"][0]


def test_first_override_records_the_llm_classification(tmp_path, monkeypatch):
    u = run(tmp_path, monkeypatch,
            {"unit_id": UID, "category": "computable", "family": "stopped_leave_double_pay", "review": "pending"},
            {"unit_id": UID, "verdict": "overridden", "category": "computable", "family": "holiday_work_double_pay", "note": "併入 39/1"})
    assert u["override"]["from"] == {"category": "computable", "family": "stopped_leave_double_pay"}
    assert u["family"] == "holiday_work_double_pay" and u["review"] == "overridden"


def test_re_overriding_keeps_the_original_llm_classification(tmp_path, monkeypatch):
    u = run(tmp_path, monkeypatch,
            {"unit_id": UID, "category": "computable", "family": "holiday_work_double_pay", "review": "overridden",
             "reviewer": "Calvin Ku", "reviewed_at": "2026-10-08", "review_note": "舊備註",
             "override": {"from": {"category": "computable", "family": "stopped_leave_double_pay"}}},
            {"unit_id": UID, "verdict": "overridden", "category": "computable", "family": "holiday_work_double_pay", "note": "訂正後的備註"})
    assert u["override"]["from"] == {"category": "computable", "family": "stopped_leave_double_pay"}
    assert u["review_note"] == "訂正後的備註" and u["reviewed_at"] == "2026-10-10"
