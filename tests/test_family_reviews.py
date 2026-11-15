"""family 簽核紀錄（silver/families/<id>/reviews.yaml）與由它推得的 family 狀態。

簽核對象是決定題目與答案的四個 family 檔：spec.yaml、rule.py、templates.yaml、sampler.py。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import family_reviews as fr  # noqa: E402

SIGNED_FILES = ("spec.yaml", "rule.py", "templates.yaml", "sampler.py")


def make_family(tmp_path: Path, src: str = "annual_leave_days") -> Path:
    """複製一個真 family 的四個簽核檔到暫存目錄，測試不碰 repo 內的檔案。"""
    fdir = tmp_path / src
    fdir.mkdir()
    for name in SIGNED_FILES:
        shutil.copy(ROOT / "silver/families" / src / name, fdir / name)
    return fdir


def write_reviews(fdir: Path, entries: list[dict]) -> None:
    (fdir / "reviews.yaml").write_text(yaml.safe_dump({"reviews": entries}, allow_unicode=True, sort_keys=False))


def approval(hashes: dict, **over) -> dict:
    e = {"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", **hashes,
         "verdict": "approved", "notes": "逐條對照法源"}
    e.update(over)
    return e


def append(path: Path, text: str) -> None:
    with path.open("a") as fh:
        fh.write(text)


def test_hashes_cover_the_four_files_that_decide_questions_and_answers(tmp_path):
    fdir = make_family(tmp_path)
    assert list(fr.family_hashes(fdir)) == ["spec_sha256", "rule_sha256", "templates_sha256", "sampler_sha256"]
    assert fr.HASH_FIELDS == ("spec_sha256", "rule_sha256", "templates_sha256", "sampler_sha256")


def test_no_reviews_file_means_draft(tmp_path):
    fdir = make_family(tmp_path)
    st = fr.family_status(fdir)
    assert st["status"] == "draft" and st["review"] is None


def test_matching_approved_review_makes_family_reviewed(tmp_path):
    fdir = make_family(tmp_path)
    write_reviews(fdir, [approval(fr.family_hashes(fdir))])
    st = fr.family_status(fdir)
    assert st["status"] == "reviewed"
    assert st["review"]["reviewer"] == "Calvin Ku" and st["review"]["date"] == "2026-10-12"


def test_adding_a_review_does_not_change_any_signed_hash(tmp_path):
    fdir = make_family(tmp_path)
    before = fr.family_hashes(fdir)
    write_reviews(fdir, [approval(before)])
    assert fr.family_hashes(fdir) == before


@pytest.mark.parametrize("changed", SIGNED_FILES)
def test_review_is_invalidated_when_any_signed_file_changes(tmp_path, changed):
    """過去的教訓：只改模板就能改掉題目，所以模板與 sampler 也在簽核對象裡。"""
    fdir = make_family(tmp_path)
    write_reviews(fdir, [approval(fr.family_hashes(fdir))])
    append(fdir / changed, "\n# 任何改動都讓 hash 變，舊簽核不再對應\n")
    assert fr.family_status(fdir)["status"] == "draft"


def test_latest_matching_review_wins(tmp_path):
    fdir = make_family(tmp_path)
    h = fr.family_hashes(fdir)
    write_reviews(fdir, [approval(h), approval(h, date="2026-10-13", verdict="changes_requested", notes="G05 要再對試算器")])
    assert fr.family_status(fdir)["status"] == "draft"
    write_reviews(fdir, [approval(h, date="2026-10-13", verdict="changes_requested"), approval(h, date="2026-10-14")])
    assert fr.family_status(fdir)["status"] == "reviewed"


def test_status_for_judges_the_hashes_it_is_given_not_the_current_files(tmp_path):
    """aggregate 用 manifest 上的 hash 問「產生這批題目的版本有沒有簽過」，不是問目前的檔案。"""
    fdir = make_family(tmp_path)
    old = fr.family_hashes(fdir)
    write_reviews(fdir, [approval(old)])
    append(fdir / "templates.yaml", "\n# 之後的改動\n")
    assert fr.status_for(fdir, old)["status"] == "reviewed"
    assert fr.family_status(fdir)["status"] == "draft"


def test_status_for_accepts_a_manifest_and_ignores_its_other_fields(tmp_path):
    fdir = make_family(tmp_path)
    h = fr.family_hashes(fdir)
    write_reviews(fdir, [approval(h)])
    manifest = {"family": "annual_leave_days", "family_version": "0.1.2", "n": 200, **h}
    assert fr.status_for(fdir, manifest)["status"] == "reviewed"


def test_a_manifest_without_the_new_hashes_is_never_signed(tmp_path):
    """簽核對象擴充到模板與 sampler 之前產生的 manifest 沒有 templates／sampler hash，不能被當成已簽核。"""
    fdir = make_family(tmp_path)
    h = fr.family_hashes(fdir)
    write_reviews(fdir, [approval(h)])
    old_manifest = {"spec_sha256": h["spec_sha256"], "rule_sha256": h["rule_sha256"]}
    assert fr.status_for(fdir, old_manifest)["status"] == "draft"


@pytest.mark.parametrize("field,value,needle", [
    ("rule_sha256", None, "rule_sha256"),
    ("templates_sha256", None, "templates_sha256"),
    ("sampler_sha256", "abc123", "sampler_sha256"),
    ("verdict", "lgtm", "verdict"),
    ("spec_sha256", "abc123", "spec_sha256"),
    ("date", "10/12/2026", "date"),
    ("reviewer", "", "reviewer"),
])
def test_malformed_review_entry_is_rejected_with_the_field_named(tmp_path, field, value, needle):
    fdir = make_family(tmp_path)
    e = approval(fr.family_hashes(fdir))
    if value is None:
        e.pop(field)
    else:
        e[field] = value
    write_reviews(fdir, [e])
    with pytest.raises(ValueError, match=needle):
        fr.load_reviews(fdir)


def test_unquoted_yaml_date_is_accepted(tmp_path):
    fdir = make_family(tmp_path)
    hs = ", ".join(f"{k}: {v}" for k, v in fr.family_hashes(fdir).items())
    (fdir / "reviews.yaml").write_text(
        f"reviews:\n  - {{reviewer: Calvin Ku, role: owner, date: 2026-10-12, {hs}, verdict: approved}}\n")
    assert fr.family_status(fdir)["review"]["date"] == "2026-10-12"


def _latest_sets():
    import dataset_registry
    return [(rel, mf) for _, rel in dataset_registry.load().latest_releases()
            for mf in sorted((ROOT / "gold/releases" / rel / "public").glob("*.manifest.json"))]


@pytest.mark.parametrize("release, mf", _latest_sets(), ids=lambda x: x if isinstance(x, str) else x.name)
def test_family_hashes_match_what_generate_wrote_into_the_manifest(release, mf):
    """簽核對象必須和題目 lineage 用同一套 hash，否則簽了也對不上 release。每個資料集的最新 release。"""
    m = json.loads(mf.read_text())
    fid = m["family"]
    hashes = fr.family_hashes(ROOT / "silver/families" / fid)
    assert hashes == {k: m.get(k) for k in fr.HASH_FIELDS}


@pytest.mark.parametrize("fid", [f for f in ["overtime_pay", "annual_leave_days", "ban_checksum", "minguo_year"]
                                 if (ROOT / "silver/families" / f / "spec.yaml").exists()])
def test_specs_do_not_carry_review_state(fid):
    """簽核只在 reviews.yaml；spec.yaml 再出現 status／review_log 會讓人誤以為那裡才算數。"""
    spec = yaml.safe_load((ROOT / "silver/families" / fid / "spec.yaml").read_text())
    assert "status" not in spec and "review_log" not in spec
