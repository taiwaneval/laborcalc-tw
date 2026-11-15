"""資料集登錄（datasets.yaml）：每個 family 登記在恰好一個資料集，每個 release 屬於一個資料集。"""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry as dr  # noqa: E402


def write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "datasets.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def two_datasets() -> dict:
    """LaborCalc-TW 加一個假的第二個資料集（真的第二個資料集的 id 與名稱尚未決定）。"""
    return {"datasets": {
        "laborcalc": {"name": "LaborCalc-TW", "families": ["overtime_pay", "annual_leave_days"],
                      "releases": ["2026Q4", "laborcalc-2027Q1"], "legacy_releases": ["2026Q4"],
                      "release_name_format": "laborcalc-{period}", "default_weights": {},
                      "public": {"github": "taiwaneval/laborcalc-tw", "huggingface": "taiwaneval/LaborCalc-TW",
                                 "templates": "tools/public_export"}},
        "fakeset": {"name": "FakeSet-TW", "families": ["fake_family"], "releases": ["fakeset-2027Q1"],
                    "release_name_format": "fakeset-{period}", "default_weights": {"fake_family": 2.0},
                    "public": {"github": "example/fakeset", "huggingface": "example/FakeSet",
                               "templates": "tools/public_export/fakeset"}},
    }}


# ---- 已提交的登錄檔
def test_committed_registry_has_laborcalc_with_2026Q4_and_its_families():
    reg = dr.load()
    ds = reg.get("laborcalc")
    assert ds.name == "LaborCalc-TW"
    # 公開匯出的登錄檔只列已出貨的 family（2026Q4 是 v1 兩個）；私有 repo 另有資遣費與預告期兩個
    assert {"overtime_pay", "annual_leave_days"} <= set(ds.families) <= {
        "overtime_pay", "annual_leave_days", "severance_pay", "termination_notice_period"}
    assert "2026Q4" in ds.releases and "2026Q4" in ds.legacy_releases
    assert (ds.github, ds.huggingface) == ("taiwaneval/laborcalc-tw", "taiwaneval/LaborCalc-TW")
    if "admincalc" not in {d.id for d in reg.datasets}:   # LaborCalc-TW 的公開 repo 只有一個資料集
        return
    adm = reg.get("admincalc")
    assert (adm.name, set(adm.families), adm.releases) == ("AdminCalc-TW", {"ban_checksum", "minguo_year"}, ())
    assert (adm.github, adm.huggingface) == ("taiwaneval/admincalc-tw", "taiwaneval/AdminCalc-TW")


def test_every_registered_family_exists_and_every_family_dir_is_registered():
    """新 family 要登記到一個資料集（docs/family-admission.md）；登記了的 family 目錄要存在。"""
    reg = dr.load()
    registered = {f for ds in reg.datasets for f in ds.families}
    on_disk = {p.name for p in (ROOT / "silver/families").iterdir() if (p / "spec.yaml").exists()}
    assert registered <= on_disk, f"登記了但沒有目錄：{sorted(registered - on_disk)}"
    assert on_disk <= registered, f"沒有登記到任何資料集：{sorted(on_disk - registered)}（加進 datasets.yaml）"


def test_every_registered_release_exists_in_gold():
    for ds in dr.load().datasets:
        for rel in ds.releases:
            assert (ROOT / "gold/releases" / rel / "public").is_dir(), f"{ds.id} 登記了 {rel}，但沒有題集"


def test_every_release_in_gold_is_registered():
    reg = dr.load()
    for p in (ROOT / "gold/releases").iterdir():
        if p.is_dir():
            assert reg.dataset_of_release(p.name) is not None, f"gold/releases/{p.name} 沒有登記在 datasets.yaml"


# ---- 查詢
def test_lookups_by_family_and_release(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    assert [d.id for d in reg.datasets] == ["laborcalc", "fakeset"]
    assert reg.dataset_of_family("fake_family").id == "fakeset"
    assert reg.dataset_of_family("overtime_pay").id == "laborcalc"
    assert reg.dataset_of_family("nope") is None
    assert reg.dataset_of_release("2026Q4").id == "laborcalc"
    assert reg.dataset_of_release("fakeset-2027Q1").id == "fakeset"
    assert reg.dataset_of_release("2099Q1") is None
    assert reg.get("laborcalc").latest_release == "laborcalc-2027Q1"
    assert reg.get("fakeset").default_weights == {"fake_family": 2.0}
    with pytest.raises(dr.RegistryError, match="nope"):
        reg.get("nope")


def test_release_name_comes_from_the_dataset_format(tmp_path):
    ds = dr.load(write(tmp_path, two_datasets())).get("laborcalc")
    assert ds.release_name("2027Q2") == "laborcalc-2027Q2"
    with pytest.raises(dr.RegistryError):
        ds.release_name("2027-Q2")


# ---- release 歸屬
def test_resolve_release_returns_the_one_dataset_of_its_families(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    assert reg.resolve_release("2026Q4", ["overtime_pay", "annual_leave_days"]).id == "laborcalc"
    assert reg.resolve_release("fakeset-2027Q1", ["fake_family"]).id == "fakeset"
    assert reg.resolve_release("scratch", ["fake_family"]).id == "fakeset"      # 沒登記的 release 由 family 推得


def test_resolve_release_refuses_families_of_two_datasets(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    with pytest.raises(dr.RegistryError, match="兩個以上的資料集|LaborCalc-TW.*FakeSet-TW|FakeSet-TW.*LaborCalc-TW"):
        reg.resolve_release("2026Q4", ["overtime_pay", "fake_family"])


def test_resolve_release_refuses_a_family_of_another_dataset_in_a_registered_release(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    with pytest.raises(dr.RegistryError, match="fakeset-2027Q1"):
        reg.resolve_release("fakeset-2027Q1", ["overtime_pay"])


def test_resolve_release_refuses_an_unregistered_family(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    with pytest.raises(dr.RegistryError, match="ban_checksum"):
        reg.resolve_release("2026Q4", ["overtime_pay", "ban_checksum"])


# ---- 登錄檔本身的檢查
@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["datasets"]["fakeset"]["families"].append("overtime_pay"), "overtime_pay"),
    (lambda d: d["datasets"]["fakeset"]["releases"].append("2026Q4"), "2026Q4"),
    (lambda d: d["datasets"]["fakeset"]["releases"].append("2027Q2"), "fakeset-{period}"),
    (lambda d: d["datasets"]["fakeset"].pop("name"), "name"),
    (lambda d: d["datasets"]["fakeset"]["default_weights"].update({"overtime_pay": 1.0}), "default_weights"),
    (lambda d: d["datasets"]["fakeset"].update({"release_name_format": "fakeset"}), "{period}"),
    (lambda d: d["datasets"]["fakeset"]["public"].update({"github": "taiwaneval/laborcalc-tw"}), "taiwaneval/laborcalc-tw"),
    (lambda d: d["datasets"]["fakeset"].update({"name": "LaborCalc-TW"}), "LaborCalc-TW"),
    (lambda d: d["datasets"].update({"Bad Id": d["datasets"].pop("fakeset")}), "Bad Id"),
])
def test_invalid_registries_are_refused(tmp_path, mutate, msg):
    doc = two_datasets()
    mutate(doc)
    with pytest.raises(dr.RegistryError, match=msg.replace("{", r"\{").replace("}", r"\}")):
        dr.load(write(tmp_path, doc))


def test_subset_keeps_only_one_dataset_and_round_trips(tmp_path):
    reg = dr.load(write(tmp_path, two_datasets()))
    text = reg.subset_yaml("laborcalc")
    assert "fakeset" not in text and "fake_family" not in text
    (tmp_path / "sub").mkdir()
    again = dr.load(write(tmp_path / "sub", yaml.safe_load(text)))
    assert [d.id for d in again.datasets] == ["laborcalc"]
    assert again.get("laborcalc") == reg.get("laborcalc")


def test_cli_prints_the_latest_release_of_a_familys_dataset(tmp_path, capsys):
    p = write(tmp_path, two_datasets())
    assert dr.main(["latest-release", "--family", "fake_family", "--registry", str(p)]) == 0
    assert capsys.readouterr().out.strip() == "fakeset-2027Q1"
    assert dr.main(["latest-release", "--dataset", "laborcalc", "--registry", str(p)]) == 0
    assert capsys.readouterr().out.strip() == "laborcalc-2027Q1"


def test_subset_can_keep_only_the_released_families(tmp_path):
    """公開匯出只帶 release 用到的 family；已登記但還沒出貨的 family 不列進公開版的登錄檔。"""
    reg = dr.load(write(tmp_path, two_datasets()))
    fams = list(reg.get("laborcalc").families)
    sub = dr.loads(reg.subset_yaml("laborcalc", families=fams[:1]))
    assert sub.get("laborcalc").families == tuple(fams[:1])
    with pytest.raises(dr.RegistryError, match="fake_family"):
        reg.subset_yaml("laborcalc", families=["fake_family"])
