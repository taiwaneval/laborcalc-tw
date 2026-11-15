"""gold/generate.py 的 CLI：--out-dir 把題集寫到別處，不動 gold/releases（tools/reproduce.py 用它重生後比對）。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry  # noqa: E402

RELEASE = dataset_registry.load().latest_release_of_family("overtime_pay")   # LaborCalc-TW 的最新 release
REL = ROOT / "gold/releases" / RELEASE / "public"


def test_out_dir_writes_the_set_there_and_leaves_the_release_alone(tmp_path):
    m = json.loads((REL / "overtime_pay.manifest.json").read_text())
    before = {p.name: p.read_bytes() for p in REL.iterdir()}
    r = subprocess.run([sys.executable, str(ROOT / "gold/generate.py"), "overtime_pay", "--release", RELEASE, "--split", "public",
                        "--n", str(m["n"] + m["rejected"]), "--as-of", m["as_of"], "--out-dir", str(tmp_path / "out"),
                        "--pin-generator-version", m["generator_version"]],   # release 釘住自己的產生器版本
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for ext in ("jsonl", "coverage.json", "manifest.json"):
        assert (tmp_path / "out" / f"overtime_pay.{ext}").read_bytes() == (REL / f"overtime_pay.{ext}").read_bytes()
    assert {p.name: p.read_bytes() for p in REL.iterdir()} == before


def test_overtime_items_carry_the_minimum_wage_table(tmp_path):
    """2025 年起依《最低工資法》，題目 metadata 的費率表叫 minimum_wage，不再叫 basic_wage（產生器 0.4.0）。"""
    r = subprocess.run([sys.executable, str(ROOT / "gold/generate.py"), "overtime_pay", "--release", RELEASE, "--split", "public",
                        "--n", "20", "--as-of", "2026-10-08", "--out-dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    items = [json.loads(l) for l in (tmp_path / "overtime_pay.jsonl").read_text().splitlines()]
    assert {json.dumps(it["rate_table"], sort_keys=True) for it in items} == {
        json.dumps({"id": "minimum_wage", "effective_from": "2026-01-01", "monthly": 29500}, sort_keys=True)}
