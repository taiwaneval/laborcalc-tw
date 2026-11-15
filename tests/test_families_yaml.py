"""silver/inventory/<pcode>/families.yaml 與 family spec 的一致性。"""
from pathlib import Path

import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry as dr  # noqa: E402
MAPS = sorted((ROOT / "silver/inventory").glob("*/families.yaml"))


def entries():
    return [(m, f) for m in MAPS for f in yaml.safe_load(m.read_text())["families"]]


def test_families_yaml_versions_match_their_specs():
    """公開匯出只帶已出貨的 family，盤點的 families.yaml 卻列著同一部法規的所有 family；
    沒有登記在本 repo datasets.yaml 的 family 略過（私有 repo 每個 family 都登記，見 test_dataset_registry）。"""
    registered = {fid for ds in dr.load().datasets for fid in ds.families}
    for m, f in entries():
        if f["id"] not in registered:
            assert not (ROOT / "silver" / f["spec"]).exists(), f"{f['id']} 有 spec 卻沒登記到資料集"
            continue
        spec = yaml.safe_load((ROOT / "silver" / f["spec"]).read_text())
        assert f["id"] == spec["id"], m
        assert f["version"] == spec["version"], f"{m.parent.name}/families.yaml 寫 {f['id']} {f['version']}，spec 是 {spec['version']}"


def test_families_yaml_does_not_keep_its_own_status():
    """family 狀態只由 silver/families/<id>/reviews.yaml 推得；這裡再存一份會在簽核後過期。"""
    assert [f["id"] for _, f in entries() if "status" in f] == []


def test_every_family_has_lineage_in_some_families_yaml():
    """沒有對應的 family 產生的題目 lineage 會是空的，追不到法條。
    來源不是法規的 family 沒有 inventory，改在 spec 的 bronze_sources 列出來源（docs/bronze-non-law-sources.md 第 4 節；
    admission A7 不適用），gold/generate.py 由它寫 lineage.sources。兩者都沒有的 family 仍然不通過。"""
    specs = {p.name: yaml.safe_load((p / "spec.yaml").read_text()) for p in (ROOT / "silver/families").iterdir()
             if (p / "spec.yaml").exists()}
    law_based = {fid for fid, s in specs.items() if not s.get("bronze_sources")}
    assert law_based <= {f["id"] for _, f in entries()}
