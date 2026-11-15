"""site-data 與資料集登錄：每個資料集各自產生網站資料，互不影響；index/datasets.json 列出所有資料集。

用假的第二個資料集（fakeset，family fake_family 複製自 annual_leave_days）在暫存 repo 裡測。"""
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

_s = importlib.util.spec_from_file_location("site_data_datasets_under_test", ROOT / "tools/site_data.py")
sd = importlib.util.module_from_spec(_s); _s.loader.exec_module(sd)

FAKE, FAKE_REL, SRC_FID = "fake_family", "fakeset-2099Q1", "annual_leave_days"
LATEST = {d.id: d.latest_release for d in dr.load().datasets}


def _copy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for d in ("bronze", "silver", "gold", "index"):
        shutil.copytree(ROOT / d, repo / d, ignore=shutil.ignore_patterns("__pycache__", "prompts", "private"))
    shutil.copy(ROOT / "datasets.yaml", repo / "datasets.yaml")
    (repo / "eval").mkdir()
    if (ROOT / "eval/logs").exists():                       # 公開版不帶原始評測紀錄，只有逐題得分表
        (repo / "eval/logs").symlink_to(ROOT / "eval/logs")
    if (ROOT / "eval/scores").exists():
        shutil.copytree(ROOT / "eval/scores", repo / "eval/scores")
    return repo


def _register_fakeset(repo: Path) -> None:
    doc = yaml.safe_load((repo / "datasets.yaml").read_text(encoding="utf-8"))
    doc["datasets"]["fakeset"] = {"name": "FakeSet-TW", "families": [FAKE], "releases": [FAKE_REL],
                                  "release_name_format": "fakeset-{period}",
                                  "public": {"github": "example/fakeset", "huggingface": "example/FakeSet",
                                             "templates": "tools/public_export/fakeset"}}
    (repo / "datasets.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _fake_release(repo: Path, families=(FAKE,), index_name="FakeSet-TW") -> None:
    """fakeset-2099Q1：fake_family 的 public 題集（annual_leave_days 2026Q4 題集改名）與沒有任何 target 的 index。"""
    if not (repo / "silver/families" / FAKE).exists():
        shutil.copytree(repo / "silver/families" / SRC_FID, repo / "silver/families" / FAKE)
    src = repo / "gold/releases" / LATEST["laborcalc"] / "public"
    dst = repo / "gold/releases" / FAKE_REL / "public"
    dst.mkdir(parents=True, exist_ok=True)
    m = json.loads((src / f"{SRC_FID}.manifest.json").read_text(encoding="utf-8"))
    m |= {"family": FAKE, "release": FAKE_REL}
    (dst / f"{FAKE}.manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    shutil.copy(src / f"{SRC_FID}.jsonl", dst / f"{FAKE}.jsonl")
    ix = json.loads((repo / "index" / LATEST["laborcalc"] / "index.json").read_text(encoding="utf-8"))
    ix |= {"index_name": index_name, "release": FAKE_REL, "families": list(families), "targets": {},
           "weights": {f: 1.0 for f in families}, "pins": {}}
    (repo / "index" / FAKE_REL).mkdir(parents=True, exist_ok=True)
    (repo / "index" / FAKE_REL / "index.json").write_text(json.dumps(ix, ensure_ascii=False), encoding="utf-8")


def test_each_dataset_builds_its_own_site_data_and_does_not_change_the_other(tmp_path):
    repo = _copy_repo(tmp_path)
    labor = LATEST["laborcalc"]
    before = sd.render(repo, labor)
    _register_fakeset(repo)
    _fake_release(repo)
    fake = sd.render(repo, FAKE_REL)
    assert sd.render(repo, labor) == before                                  # 另一個資料集的 release 不影響
    fams = json.loads(fake["families.json"])["families"]
    assert [f["id"] for f in fams] == [FAKE]
    res = json.loads(fake["results.json"])
    assert (res["index_name"], res["release"], res["families"]) == ("FakeSet-TW", FAKE_REL, [FAKE])
    assert not any(f"/{labor}/" in s for s in json.loads(fake["manifest.json"])["sources"])


def test_a_release_whose_index_mixes_two_datasets_is_refused(tmp_path):
    repo = _copy_repo(tmp_path)
    _register_fakeset(repo)
    _fake_release(repo, families=(SRC_FID, FAKE))
    with pytest.raises(dr.RegistryError, match="混了兩個以上的資料集"):
        sd.render(repo, FAKE_REL)
    assert any("混了兩個以上的資料集" in p for p in sd.check(repo, FAKE_REL))


def test_an_index_built_under_another_dataset_name_is_refused(tmp_path):
    repo = _copy_repo(tmp_path)
    _register_fakeset(repo)
    _fake_release(repo, index_name="LaborCalc-TW")
    with pytest.raises(ValueError, match="FakeSet-TW"):
        sd.render(repo, FAKE_REL)


def test_dataset_option_builds_the_datasets_latest_release(tmp_path, monkeypatch):
    repo = _copy_repo(tmp_path)
    _register_fakeset(repo)
    _fake_release(repo)
    monkeypatch.setattr(sd, "ROOT", repo)
    assert sd.main(["--dataset", "fakeset"]) == 0
    assert json.loads((repo / "index" / FAKE_REL / "site-data/results.json").read_text())["index_name"] == "FakeSet-TW"


# ---- index/datasets.json：網站用來列出有哪些資料集
def test_catalog_lists_every_dataset_with_its_releases_and_public_names(tmp_path):
    repo = _copy_repo(tmp_path)
    _register_fakeset(repo)
    _fake_release(repo)
    cat = json.loads(sd.render_catalog(repo))
    by_id = {d["id"]: d for d in cat["datasets"]}
    assert list(by_id) == [d.id for d in dr.load(repo / "datasets.yaml").datasets]   # 已登記的資料集依序，fakeset 在最後
    assert list(by_id)[-1] == "fakeset"
    assert (by_id["fakeset"]["name"], by_id["fakeset"]["latest_release"], by_id["fakeset"]["families"]) == ("FakeSet-TW", FAKE_REL, [FAKE])
    assert by_id["laborcalc"]["public"] == {"github": "taiwaneval/laborcalc-tw", "huggingface": "taiwaneval/LaborCalc-TW"}
    rel = {r["release"]: r for r in by_id["laborcalc"]["releases"]}[LATEST["laborcalc"]]
    assert rel["site_data"] == f"index/{LATEST['laborcalc']}/site-data/"
    assert {r["release"]: r for r in by_id["fakeset"]["releases"]}[FAKE_REL]["site_data"] is None   # 還沒產生 bundle
    assert sd.render_catalog(repo) == sd.render_catalog(repo)                   # 決定性


def test_catalog_check_catches_a_registry_change(tmp_path):
    repo = _copy_repo(tmp_path)
    (repo / "index/datasets.json").write_bytes(sd.render_catalog(repo))
    assert sd.check_catalog(repo) == []
    _register_fakeset(repo)
    _fake_release(repo)
    assert sd.check_catalog(repo) and "datasets.json" in sd.check_catalog(repo)[0]


def test_committed_catalog_is_current():
    assert sd.check_catalog(ROOT) == []
