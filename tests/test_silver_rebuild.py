"""silver 能從 bronze 與保存的輸入重建，結果與已提交的逐 byte 相同。

  inventory.json、prompts/   由 bronze/<pcode>/source.json 決定（build_inventory → triage --stage heuristic）
  triage.yaml               由 inventory、llm_labels.json（LLM 第一輪輸出，作為輸入保存）與 reviews/*.yaml（人工決定）決定：
                            從零 merge（全部 pending），再依檔名順序套用每個 reviews 檔
tools/reproduce.py 在乾淨 clone 上用同樣的 CLI 步驟重建並比對。
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import paths  # noqa: E402

INVENTORIED = sorted(p.name for p in (ROOT / "silver/inventory").iterdir() if (p / "inventory.json").exists())
PCODES = sorted(p.name for p in (ROOT / "silver/inventory").iterdir() if (p / "triage.yaml").exists())


def load(name: str):
    s = importlib.util.spec_from_file_location(f"{name}_rebuild", ROOT / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def inventory_copy(tmp_path: Path, monkeypatch, pcode: str, names: tuple[str, ...]) -> Path:
    d = tmp_path / "inventory" / pcode
    d.mkdir(parents=True)
    for name in names:
        src = ROOT / "silver/inventory" / pcode / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, d / name)
    monkeypatch.setattr(paths, "INVENTORY", tmp_path / "inventory")
    return d


@pytest.mark.parametrize("pcode", INVENTORIED)
def test_inventory_and_prompts_rebuild_from_bronze(tmp_path, monkeypatch, pcode):
    d = inventory_copy(tmp_path, monkeypatch, pcode, ())
    load("build_inventory").main([pcode])
    load("triage").main([pcode, "--stage", "heuristic"])
    committed = ROOT / "silver/inventory" / pcode
    assert (d / "inventory.json").read_bytes() == (committed / "inventory.json").read_bytes()
    assert sorted(p.name for p in (d / "prompts").iterdir()) == sorted(p.name for p in (committed / "prompts").iterdir())
    for p in (committed / "prompts").iterdir():
        assert (d / "prompts" / p.name).read_bytes() == p.read_bytes(), p.name


@pytest.mark.parametrize("pcode", PCODES)
def test_triage_rebuilds_from_labels_and_reviews_in_filename_order(tmp_path, monkeypatch, pcode):
    d = inventory_copy(tmp_path, monkeypatch, pcode, ("inventory.json", "llm_labels.json", "reviews"))
    load("triage").main([pcode, "--stage", "merge"])
    apply_review = load("apply_review")
    for rv in sorted((d / "reviews").glob("*.yaml")):
        apply_review.main([pcode, str(rv)])
    assert (d / "triage.yaml").read_bytes() == (ROOT / "silver/inventory" / pcode / "triage.yaml").read_bytes()


def test_triage_version_comes_from_the_code_not_the_previous_file(tmp_path, monkeypatch):
    """triage_version 是分類程序（heuristic、prompt、merge）的版本，跟 GENERATOR_VERSION 一樣寫在程式裡；
    之前只從上一版 triage.yaml 沿用，從零重建會變成 0.0.0。"""
    pcode = PCODES[0]
    d = inventory_copy(tmp_path, monkeypatch, pcode, ("inventory.json", "llm_labels.json"))
    (d / "triage.yaml").write_text(yaml.safe_dump({"pcode": pcode, "triage_version": "0.0.9", "units": []}))
    triage = load("triage")
    triage.main([pcode, "--stage", "merge"])
    assert yaml.safe_load((d / "triage.yaml").read_text())["triage_version"] == triage.TRIAGE_VERSION
