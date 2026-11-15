"""gold/generate.py 寫進題目與 manifest 的 family_status 由 reviews.yaml 推得，不讀 spec.yaml 的 status。"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import family_reviews as fr  # noqa: E402
import paths  # noqa: E402

FID = "annual_leave_days"


def load_generate():
    s = importlib.util.spec_from_file_location("gen_review_status", ROOT / "gold/generate.py")
    gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen); return gen


def families_copy(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "families"
    shutil.copytree(ROOT / "silver/families" / FID, root / FID, ignore=shutil.ignore_patterns("__pycache__", "reviews.yaml"))  # 未簽核起點
    monkeypatch.setattr(paths, "FAMILIES", root)
    return root / FID


def generated_statuses(tmp_path) -> tuple[str, set[str]]:
    out = tmp_path / "out"
    load_generate().generate(FID, "2026Q4", "public", 20, out_dir=out, as_of="2026-10-09")
    man = json.loads((out / f"{FID}.manifest.json").read_text())
    items = [json.loads(l) for l in (out / f"{FID}.jsonl").read_text().splitlines()]
    return man["family_status"], {it["family_status"] for it in items}


def test_status_is_draft_without_a_matching_review(tmp_path, monkeypatch):
    families_copy(tmp_path, monkeypatch)
    assert generated_statuses(tmp_path) == ("draft", {"draft"})


def test_status_is_reviewed_with_a_matching_approved_review(tmp_path, monkeypatch):
    fdir = families_copy(tmp_path, monkeypatch)
    (fdir / "reviews.yaml").write_text(yaml.safe_dump({"reviews": [{
        "reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12",
        **fr.family_hashes(fdir), "verdict": "approved"}]}))
    assert generated_statuses(tmp_path) == ("reviewed", {"reviewed"})


def test_a_status_field_in_spec_yaml_is_ignored(tmp_path, monkeypatch):
    """spec.yaml 的 status 已移除；就算有人加回去，狀態仍只由 reviews.yaml 推得。"""
    fdir = families_copy(tmp_path, monkeypatch)
    spec = (fdir / "spec.yaml").read_text()
    assert "\nstatus:" not in spec
    (fdir / "spec.yaml").write_text(spec.replace("\ntier:", "\nstatus: reviewed\ntier:", 1))
    assert "\nstatus: reviewed\n" in (fdir / "spec.yaml").read_text()
    assert generated_statuses(tmp_path) == ("draft", {"draft"})


def generated_lineage(tmp_path, tag: str) -> tuple[dict, list[dict]]:
    out = tmp_path / f"out-{tag}"
    load_generate().generate(FID, "2026Q4", "public", 20, out_dir=out, as_of="2026-10-09")
    man = json.loads((out / f"{FID}.manifest.json").read_text())
    return man, [json.loads(l) for l in (out / f"{FID}.jsonl").read_text().splitlines()]


def test_items_and_manifest_carry_all_four_signed_hashes(tmp_path, monkeypatch):
    """templates.yaml 與 sampler.py 的 hash 進每題與 manifest 的 lineage。"""
    fdir = families_copy(tmp_path, monkeypatch)
    man, items = generated_lineage(tmp_path, "a")
    want = fr.family_hashes(fdir)
    assert {k: man[k] for k in fr.HASH_FIELDS} == want
    assert all({k: it[k] for k in fr.HASH_FIELDS} == want for it in items)
    assert man["generator_version"] == "0.6.0"


def test_editing_only_the_templates_changes_the_lineage(tmp_path, monkeypatch):
    fdir = families_copy(tmp_path, monkeypatch)
    before, _ = generated_lineage(tmp_path, "before")
    with (fdir / "templates.yaml").open("a") as fh:
        fh.write("\n# 只改模板\n")
    after, _ = generated_lineage(tmp_path, "after")
    changed = {k for k in fr.HASH_FIELDS if before[k] != after[k]}
    assert changed == {"templates_sha256"}
