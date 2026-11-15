"""題幹措辭的回歸測試：題幹字面意思必須和 gold 的算法一致。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generated(fid: str, tmp_path: Path) -> list[dict]:
    s = importlib.util.spec_from_file_location(f"gen_wording_{fid}", ROOT / "gold/generate.py")
    gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen)
    gen.generate(fid, "2026Q4", "public", 200, out_dir=tmp_path, as_of="2026-10-10")
    cov = json.loads((tmp_path / f"{fid}.coverage.json").read_text())
    assert cov["rejected"] == []
    return [json.loads(l) for l in (tmp_path / f"{fid}.jsonl").read_text().splitlines()]


def test_overtime_weekday_hours_are_always_framed_as_after_normal_hours(tmp_path):
    """平日的 hours 是正常 8 小時之後的加班時數。題幹若只寫「某個平日出勤 2 小時」，字面是當天總共只上 2 小時班。"""
    items = generated("overtime_pay", tmp_path)
    weekday = [i for i in items if i["params"]["day_type"] == "weekday" and i["params"]["hours"] > 0]
    assert weekday and [i["item_id"] for i in weekday if "下班後" not in i["prompt"]] == []


def test_overtime_stems_never_say_attendance_hours(tmp_path):
    """「出勤時數為 N 小時」會被讀成當天總出勤（2026Q4 預覽榜 T05 有 4/43 這樣誤讀）。"""
    assert [i["item_id"] for i in generated("overtime_pay", tmp_path) if "出勤時數" in i["prompt"]] == []


def test_overtime_control_stems_read_naturally_with_after_work_phrases(tmp_path):
    w0 = [i for i in generated("overtime_pay", tmp_path) if i["branch"] == "W0"]
    assert w0 and all("未延長工時" in i["prompt"] and "準時下班" not in i["prompt"] for i in w0)


def test_annual_leave_stems_do_not_suggest_the_calendar_year_system(tmp_path):
    """spec 是週年制；「當年度」暗示曆年制。"""
    assert [i["item_id"] for i in generated("annual_leave_days", tmp_path) if "當年度" in i["prompt"]] == []


def test_overtime_answer_instruction_states_the_spec_rounding():
    """題目說明的取整方式要和 spec 的 rounding.final 一致。0.2.0 改無條件進位：
    加給「以上」的法定下限是精確金額，四捨五入在小數部分小於 0.5 時會低於下限。"""
    import yaml
    spec = yaml.safe_load((ROOT / "silver/families/overtime_pay/spec.yaml").read_text())
    instruction = yaml.safe_load((ROOT / "silver/families/overtime_pay/templates.yaml").read_text())["answer_instruction"]
    assert spec["rounding"]["final"] == "ceil_to_integer"
    assert "無條件進位" in instruction and "四捨五入" not in instruction
