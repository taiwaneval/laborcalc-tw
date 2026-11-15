"""aggregate 的 release 關卡：--mode release 時，未簽核的 family、manifest 狀態過期、版本不符的 log 都會讓建置失敗。"""
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

_s = importlib.util.spec_from_file_location("aggregate_gate_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)

FID, R = "annual_leave_days", "2099Q1"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """暫存的 families／releases／logs／index，只有一個 family、一個 public manifest、沒有 log。"""
    fams = tmp_path / "families"
    (fams / FID).mkdir(parents=True)
    for name in ("spec.yaml", "rule.py", "templates.yaml", "sampler.py"):
        shutil.copy(ROOT / "silver/families" / FID / name, fams / FID / name)
    # 登錄檔：R 登記為 LaborCalc-TW 的 release（未登記的 release 本身就是 release 阻擋項，見 test_aggregate_datasets.py）
    (tmp_path / "datasets.yaml").write_text(yaml.safe_dump({"datasets": {"laborcalc": {
        "name": "LaborCalc-TW", "families": [FID], "releases": [R], "legacy_releases": [R],
        "release_name_format": "laborcalc-{period}",
        "public": {"github": "o/laborcalc", "huggingface": "o/LaborCalc", "templates": "t"}}}}))
    for attr, sub in (("FAMILIES", "families"), ("RELEASES", "releases"), ("LOGS", "logs"), ("INDEX", "index"),
                      ("REGISTRY", "datasets.yaml")):
        monkeypatch.setattr(agg, attr, tmp_path / sub)
    return tmp_path


def write_manifest(repo: Path, family_status: str) -> dict:
    m = {"family": FID, "family_version": "0.1.2", "family_status": family_status,
         **fr.family_hashes(repo / "families" / FID), "generator_version": "0.3.0",
         "release": R, "split": "public", "salt": "test", "n": 200, "rejected": 0,
         "lineage": {"laws": {}}, "as_of": "2099-01-01"}
    d = repo / "releases" / R / "public"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{FID}.manifest.json").write_text(json.dumps(m))
    return m


def sign(repo: Path, verdict: str = "approved") -> None:
    (repo / "families" / FID / "reviews.yaml").write_text(yaml.safe_dump({"reviews": [{
        "reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12",
        **fr.family_hashes(repo / "families" / FID), "verdict": verdict}]}))


def index_path(repo: Path) -> Path:
    return repo / "index" / R / "index.json"


def test_release_mode_refuses_an_unsigned_family_and_writes_nothing(repo):
    write_manifest(repo, "draft")
    with pytest.raises(agg.ReleaseGateError) as e:
        agg.aggregate(R, mode="release")
    assert any(FID in b and "reviews.yaml" in b for b in e.value.blockers)
    assert not index_path(repo).exists()


def test_release_mode_refuses_a_manifest_generated_before_sign_off(repo):
    write_manifest(repo, "draft")
    sign(repo)
    with pytest.raises(agg.ReleaseGateError) as e:
        agg.aggregate(R, mode="release")
    msg = " ".join(e.value.blockers)
    assert "重生 gold" in msg and f"gold/generate.py {FID} --release {R} --split public --n 200 --as-of 2099-01-01" in msg


def test_changes_requested_is_not_a_sign_off(repo):
    write_manifest(repo, "draft")
    sign(repo, verdict="changes_requested")
    with pytest.raises(agg.ReleaseGateError):
        agg.aggregate(R, mode="release")


def test_release_mode_passes_when_signed_and_the_manifest_is_current(repo):
    write_manifest(repo, "reviewed")
    sign(repo)
    idx = agg.aggregate(R, mode="release")
    assert idx["mode"] == "release" and idx["release_blockers"] == []
    pin = json.loads(index_path(repo).read_text())["pins"][FID]["public"]
    assert pin["family_status"] == "reviewed" and pin["review"]["reviewer"] == "Calvin Ku"
    assert pin["as_of"] == "2099-01-01" and pin["rejected"] == 0          # 第三方重生題集要用同一個 as_of


def test_preview_mode_writes_the_index_and_lists_what_blocks_a_release(repo):
    write_manifest(repo, "draft")
    idx = agg.aggregate(R)                      # 預設 preview，行為和以前一樣：照寫 index
    saved = json.loads(index_path(repo).read_text())
    assert idx["mode"] == saved["mode"] == "preview"
    assert len(saved["release_blockers"]) == 1 and FID in saved["release_blockers"][0]
    assert saved["pins"][FID]["public"]["family_status"] == "draft" and saved["pins"][FID]["public"]["review"] is None


def test_pin_status_follows_reviews_even_if_the_manifest_snapshot_is_stale(repo):
    write_manifest(repo, "draft")
    sign(repo)
    agg.aggregate(R)
    assert json.loads(index_path(repo).read_text())["pins"][FID]["public"]["family_status"] == "reviewed"


def test_a_log_run_on_another_spec_or_rule_blocks_a_release(repo):
    m = write_manifest(repo, "reviewed")
    sign(repo)
    runs = {("some-model@api@x@direct", FID, "public"): {
        "log_file": "2099-01-01_old.eval",
        "spec_mismatch": {"log_has": [{"family_version": "0.1.0", "spec_sha256": "0" * 64, "rule_sha256": "1" * 64}]}}}
    blockers = agg.release_blockers(R, {(FID, "public"): m}, runs)
    assert len(blockers) == 1 and "2099-01-01_old.eval" in blockers[0] and "some-model" in blockers[0]


def test_version_fields_cover_family_version_the_four_signed_hashes_and_the_generator():
    assert agg.VERSION_FIELDS == ("family_version", "spec_sha256", "rule_sha256", "templates_sha256",
                                  "sampler_sha256", "generator_version")


@pytest.mark.parametrize("field", ["family_version", "spec_sha256", "rule_sha256", "templates_sha256",
                                   "sampler_sha256", "generator_version"])
def test_version_check_flags_a_log_that_differs_in_any_one_field(repo, field):
    """只改模板也要被抓到：log 的題目和當期題集不是同一版。"""
    m = write_manifest(repo, "reviewed")
    same = tuple(m[k] for k in agg.VERSION_FIELDS)
    other = tuple("x" if k == field else m[k] for k in agg.VERSION_FIELDS)
    assert agg.log_version_mismatch({same}, m) is None
    mis = agg.log_version_mismatch({other}, m)
    assert mis["log_has"][0][field] == "x" and mis["manifest"][field] == m[field]
    assert mis["differs"] == [field]


def test_unknown_mode_is_rejected(repo):
    write_manifest(repo, "draft")
    with pytest.raises(ValueError, match="mode"):
        agg.aggregate(R, mode="ship-it")


def test_cli_release_mode_exits_nonzero_and_prints_blockers(repo, capsys):
    write_manifest(repo, "draft")
    assert agg.main(["--release", R, "--mode", "release"]) == 1
    out = capsys.readouterr().out
    assert "release 關卡未通過" in out and FID in out
    assert not index_path(repo).exists()


def test_version_check_tolerates_logs_where_some_items_lack_a_hash(repo):
    """舊 log 可能有題目沒帶 rule_sha256（None）；和有值的題目混在一起時不能因排序而崩潰。"""
    m = write_manifest(repo, "reviewed")
    row = lambda tpl: tuple(tpl if k == "templates_sha256" else m[k] for k in agg.VERSION_FIELDS)  # noqa: E731
    mis = agg.log_version_mismatch({row(None), row("f" * 64)}, m)
    assert {x["templates_sha256"] for x in mis["log_has"]} == {None, "f" * 64}


def _two_family_manifests(repo: Path) -> dict:
    """同一個 split 有兩個 family 的 manifest（第二個借用同一份簽核，只看 target 的阻擋項）。"""
    m = write_manifest(repo, "reviewed")
    sign(repo)
    return {(FID, "public"): m, ("zz_other", "public"): {**m, "family": "zz_other"}}


CURRENT_RUN = {"log_file": "2099-01-02_current.eval", "spec_mismatch": None, "fake": None, "repeats": {"n_runs": 1}}


def test_a_model_target_without_a_run_on_one_family_blocks_a_release(repo):
    """只跑了一個 family 的 target 不能進 release：另一個 family 沒有 log，或 log 還在寫（status=started，
    collect_runs 不採用）時，runs 裡根本沒有那個 key，其他檢查都看不到它（2026-11-12 的 qwen3.8-27b@…@max）。"""
    manifests = _two_family_manifests(repo)
    runs = {("half@api@x@max", FID, "public"): dict(CURRENT_RUN),
            ("whole@api@x@max", FID, "public"): dict(CURRENT_RUN),
            ("whole@api@x@max", "zz_other", "public"): dict(CURRENT_RUN)}
    blockers = [b for b in agg.release_blockers(R, manifests, runs) if "@api@x@max" in b]
    assert len(blockers) == 1
    assert "half@api@x@max" in blockers[0] and "zz_other/public" in blockers[0] and "沒有" in blockers[0]


def test_a_missing_family_is_only_required_in_splits_the_target_was_run_on(repo):
    manifests = _two_family_manifests(repo)
    manifests[(FID, "private")] = {**manifests[(FID, "public")], "split": "private"}
    runs = {("whole@api@x@max", FID, "public"): dict(CURRENT_RUN),
            ("whole@api@x@max", "zz_other", "public"): dict(CURRENT_RUN)}
    assert [b for b in agg.release_blockers(R, manifests, runs) if "@api@x@max" in b] == []


def test_fake_solvers_may_cover_only_some_families(repo):
    manifests = _two_family_manifests(repo)
    runs = {("fake-oracle@none@none@direct", FID, "public"): {**CURRENT_RUN, "fake": "oracle"}}
    assert [b for b in agg.release_blockers(R, manifests, runs) if "fake-oracle" in b] == []
