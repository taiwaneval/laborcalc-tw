"""aggregate 與資料集登錄：名稱、權重從 datasets.yaml 讀；一期 release 混了兩個資料集的 family 就拒絕執行。"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry as dr  # noqa: E402
import family_reviews as fr  # noqa: E402

_s = importlib.util.spec_from_file_location("aggregate_datasets_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)

REAL, FAKE = "annual_leave_days", "fake_family"


def registry_doc() -> dict:
    return {"datasets": {
        "laborcalc": {"name": "LaborCalc-TW", "families": [REAL], "releases": ["laborcalc-2099Q1"],
                      "release_name_format": "laborcalc-{period}",
                      "public": {"github": "o/laborcalc", "huggingface": "o/LaborCalc", "templates": "t"}},
        "fakeset": {"name": "FakeSet-TW", "families": [FAKE], "releases": ["fakeset-2099Q1"],
                    "release_name_format": "fakeset-{period}", "default_weights": {FAKE: 2.0},
                    "public": {"github": "o/fakeset", "huggingface": "o/FakeSet", "templates": "t2"}},
    }}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """暫存的 families／releases／index 與登錄檔：真的 family 登記在 laborcalc，一個假 family 登記在 fakeset。"""
    for fid in (REAL, FAKE):
        (tmp_path / "families" / fid).mkdir(parents=True)
        for name in ("spec.yaml", "rule.py", "templates.yaml", "sampler.py"):
            shutil.copy(ROOT / "silver/families" / REAL / name, tmp_path / "families" / fid / name)
    reg = tmp_path / "datasets.yaml"
    reg.write_text(yaml.safe_dump(registry_doc(), sort_keys=False), encoding="utf-8")
    for attr, sub in (("FAMILIES", "families"), ("RELEASES", "releases"), ("LOGS", "logs"), ("INDEX", "index"),
                      ("REGISTRY", "datasets.yaml")):
        monkeypatch.setattr(agg, attr, tmp_path / sub)
    return tmp_path


def manifest(repo: Path, release: str, fid: str) -> None:
    m = {"family": fid, "family_version": "0.1.0", "family_status": "draft",
         **fr.family_hashes(repo / "families" / fid), "generator_version": "0.6.0",
         "release": release, "split": "public", "salt": "test", "n": 10, "rejected": 0,
         "lineage": {"laws": {}}, "as_of": "2099-01-01"}
    d = repo / "releases" / release / "public"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{fid}.manifest.json").write_text(json.dumps(m))


def test_index_name_and_weights_come_from_the_releases_dataset(repo):
    manifest(repo, "fakeset-2099Q1", FAKE)
    manifest(repo, "laborcalc-2099Q1", REAL)
    fake = agg.aggregate("fakeset-2099Q1")
    real = agg.aggregate("laborcalc-2099Q1")
    assert (fake["index_name"], fake["families"], fake["weights"]) == ("FakeSet-TW", [FAKE], {FAKE: 2.0})
    assert (real["index_name"], real["families"], real["weights"]) == ("LaborCalc-TW", [REAL], {REAL: 1.0})


def test_two_datasets_releases_do_not_touch_each_others_index(repo):
    manifest(repo, "fakeset-2099Q1", FAKE)
    manifest(repo, "laborcalc-2099Q1", REAL)
    agg.aggregate("laborcalc-2099Q1")
    before = (repo / "index/laborcalc-2099Q1/index.json").read_text()
    agg.aggregate("fakeset-2099Q1")
    assert sorted(p.name for p in (repo / "index").iterdir()) == ["fakeset-2099Q1", "laborcalc-2099Q1"]
    after = json.loads((repo / "index/laborcalc-2099Q1/index.json").read_text())
    assert after == json.loads(before)
    assert after["pins"].keys() == {REAL}


@pytest.mark.parametrize("mode", agg.MODES)
def test_a_release_mixing_two_datasets_is_refused_and_writes_nothing(repo, mode):
    manifest(repo, "laborcalc-2099Q1", REAL)
    manifest(repo, "laborcalc-2099Q1", FAKE)
    with pytest.raises(dr.RegistryError, match="混了兩個以上的資料集"):
        agg.aggregate("laborcalc-2099Q1", mode=mode)
    assert not (repo / "index/laborcalc-2099Q1").exists()
    assert agg.main(["--release", "laborcalc-2099Q1", "--mode", mode]) == 1


def test_an_unregistered_family_is_refused(repo):
    (repo / "families" / "stray").mkdir()
    for name in ("spec.yaml", "rule.py", "templates.yaml", "sampler.py"):
        shutil.copy(ROOT / "silver/families" / REAL / name, repo / "families" / "stray" / name)
    manifest(repo, "laborcalc-2099Q1", "stray")
    with pytest.raises(dr.RegistryError, match="stray"):
        agg.aggregate("laborcalc-2099Q1")


def test_an_unregistered_release_is_a_release_blocker_but_previews(repo):
    manifest(repo, "scratch", FAKE)
    idx = agg.aggregate("scratch")
    assert idx["index_name"] == "FakeSet-TW"
    assert any("datasets.yaml" in b and "scratch" in b for b in idx["release_blockers"])


@pytest.mark.parametrize("ds_id, release", [(d.id, r) for d, r in dr.load().latest_releases()])
def test_each_datasets_latest_index_resolves_to_it_with_its_name_and_weights(ds_id, release):
    """已提交的 index（如已出貨、不改名的 2026Q4）屬於它登記的資料集；index_name、權重與登錄檔相同。"""
    reg = dr.load()
    ix = json.loads((ROOT / "index" / release / "index.json").read_text(encoding="utf-8"))
    ds = reg.resolve_release(release, ix["families"])
    assert ds.id == ds_id and ix["index_name"] == ds.name
    assert ix["weights"] == {f: ds.default_weights.get(f, 1.0) for f in ix["families"]}
