import importlib.util, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def load_generate():
    s = importlib.util.spec_from_file_location("gen_al", ROOT / "gold/generate.py"); gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen); return gen


def test_annual_leave_generation_is_deterministic_and_complete(tmp_path):
    gen = load_generate()
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate("annual_leave_days", "2026Q4", "public", 200, out_dir=a, as_of="2026-10-09")
    gen.generate("annual_leave_days", "2026Q4", "public", 200, out_dir=b, as_of="2026-10-09")
    assert (a / "annual_leave_days.jsonl").read_bytes() == (b / "annual_leave_days.jsonl").read_bytes()
    cov = json.loads((a / "annual_leave_days.coverage.json").read_text())
    assert cov["n"] == 200 and cov["rejected"] == []
    assert cov["branch"] == {"S0": 10, "S1": 20, "S2": 20, "S3": 20, "S4": 20, "S5": 20, "S6": 20, "S7": 10, "B1": 25, "B2": 25, "B3": 10}
    items = [json.loads(l) for l in (a / "annual_leave_days.jsonl").read_text().splitlines()]
    assert all(it["answer_type"] == "integer_days" and it["tolerance"] == 0 and "rate_table" not in it for it in items)
    assert {it["gold"] for it in items if it["branch"] == "S0"} == {0}
    assert all(it["prompt"].endswith("請只回答一個整數（天數），不要附任何單位或說明。") for it in items)
    assert all(("anchor_months" in it["knobs"]) == it["branch"].startswith("B") for it in items)
    assert sum(cov["anchor_months"].values()) == 60
    man = json.loads((a / "annual_leave_days.manifest.json").read_text())
    assert man["lineage"]["laws"]["N0030001"]["units"] == ["N0030001/38/1"] and man["generator_version"] == "0.6.0"
