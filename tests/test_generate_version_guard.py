"""generate 的版本守門：題目變了，但 family 檔與 GENERATOR_VERSION 都沒變，就拒絕覆寫既有題集。

那種變化只可能來自共用產生器（gold/generate.py、tools/numerals.py、tools/sampling.py）。不擋的話，舊 log 的版本欄位
和新題集一模一樣，aggregate 不會把它們標成過期。
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import family_reviews as fr  # noqa: E402
import paths  # noqa: E402

FID = "annual_leave_days"


def load_generate():
    s = importlib.util.spec_from_file_location("gen_version_guard", ROOT / "gold/generate.py")
    gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen); return gen


def run(gen, out: Path) -> list[dict]:
    gen.generate(FID, "2026Q4", "public", 20, out_dir=out, as_of="2026-10-09")
    return [json.loads(l) for l in (out / f"{FID}.jsonl").read_text().splitlines()]


def shared_code_changed(gen, monkeypatch):
    """模擬共用產生器改了輸出：每題題幹多一個字。"""
    orig = gen.render
    monkeypatch.setattr(gen, "render", lambda *a, **k: (lambda r: (r[0] + "。", r[1], r[2]))(orig(*a, **k)))


def test_regenerating_unchanged_code_is_allowed(tmp_path):
    gen = load_generate()
    first = run(gen, tmp_path)
    assert run(gen, tmp_path) == first


def test_shared_code_change_without_a_version_bump_is_refused(tmp_path, monkeypatch):
    gen = load_generate()
    before = (run(gen, tmp_path), (tmp_path / f"{FID}.jsonl").read_bytes())
    shared_code_changed(gen, monkeypatch)
    with pytest.raises(SystemExit, match="GENERATOR_VERSION"):
        run(gen, tmp_path)
    assert (tmp_path / f"{FID}.jsonl").read_bytes() == before[1]          # 拒絕時不覆寫


def test_shared_code_change_with_a_version_bump_is_allowed(tmp_path, monkeypatch):
    gen = load_generate()
    run(gen, tmp_path)
    shared_code_changed(gen, monkeypatch)
    monkeypatch.setattr(gen, "GENERATOR_VERSION", "9.9.9")
    assert all(it["prompt"].endswith("。") for it in run(gen, tmp_path))


def test_regenerating_after_sign_off_is_allowed(tmp_path, monkeypatch):
    """簽核後重生只改 family_status，這是簽核流程設計的正常情況，不能被守門擋住。"""
    root = tmp_path / "families"
    shutil.copytree(ROOT / "silver/families" / FID, root / FID, ignore=shutil.ignore_patterns("__pycache__", "reviews.yaml"))  # 未簽核起點
    monkeypatch.setattr(paths, "FAMILIES", root)
    gen = load_generate()
    out = tmp_path / "out"
    assert {it["family_status"] for it in run(gen, out)} == {"draft"}
    (root / FID / "reviews.yaml").write_text(yaml.safe_dump({"reviews": [{
        "reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", **fr.family_hashes(root / FID), "verdict": "approved"}]}))
    assert {it["family_status"] for it in run(gen, out)} == {"reviewed"}
