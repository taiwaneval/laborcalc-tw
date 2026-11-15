"""tools/site_data.py：每個 release 匯出 site-data bundle。網站與報告只讀這份；每個數字都從產物來。"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
_s = importlib.util.spec_from_file_location("site_data_under_test", ROOT / "tools/site_data.py")
sd = importlib.util.module_from_spec(_s); _s.loader.exec_module(sd)

import dataset_registry  # noqa: E402

# LaborCalc-TW 的最新 release（datasets.yaml）；其他資料集的 site-data 見 test_site_data_datasets.py
RELEASE = dataset_registry.load().get("laborcalc").latest_release
FILES = ("manifest.json", "laws.json", "families.json", "results.json", "sources.json")
HAS_LOGS = (ROOT / "eval/logs" / RELEASE).is_dir()      # 公開版不帶原始評測紀錄，只有逐題得分表 eval/scores/


def _link_eval(repo: Path) -> None:
    """repo 的複本要看得到評測結果：有原始紀錄就連過去；逐題得分表一律複製（沒有紀錄時 site-data 讀它記的 sha256）。"""
    (repo / "eval").mkdir(exist_ok=True)
    if (ROOT / "eval/logs").exists():
        (repo / "eval/logs").symlink_to(ROOT / "eval/logs")
    if (ROOT / "eval/scores").exists():
        shutil.copytree(ROOT / "eval/scores", repo / "eval/scores")


def _table_hashes() -> dict[str, str]:
    doc = json.loads((ROOT / f"eval/scores/{RELEASE}/runs.json").read_text(encoding="utf-8"))
    return {f"eval/logs/{RELEASE}/{r['log_file']}": r["log_sha256"] for r in doc["runs"]}


def _log_hash(rel: str) -> str:
    """紀錄的 sha256：檔案在就實算，不在（公開版）就用得分表記的。"""
    import hashlib
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else _table_hashes()[rel]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site-data")
    sd.build(ROOT, RELEASE, out)
    return {f: json.loads((out / f).read_text(encoding="utf-8")) for f in FILES} | {"_dir": out}


def test_every_file_validates_against_the_schema(built):
    schema = json.loads((ROOT / "tools/schemas/site_data.schema.json").read_text(encoding="utf-8"))
    for f in FILES:
        sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{f.removesuffix('.json')}"}
        jsonschema.validate(built[f], sub)
    assert built["manifest.json"]["schema_version"] == sd.SCHEMA_VERSION


def test_manifest_hashes_each_output_file_and_every_source_it_read(built):
    import hashlib
    m = built["manifest.json"]
    for f in FILES[1:]:
        assert m["files"][f] == hashlib.sha256((built["_dir"] / f).read_bytes()).hexdigest()
    for rel, h in m["sources"].items():
        assert (_log_hash(rel) if rel.startswith("eval/logs/") else hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()) == h, rel
    assert f"index/{RELEASE}/index.json" in m["sources"] and "bronze/N0030001/provenance.json" in m["sources"]
    assert "silver/families/overtime_pay/reviews.yaml" in m["sources"]


def test_bronze_layer_has_source_mirror_commit_fetch_time_and_hash(built):
    law = {x["pcode"]: x for x in built["laws.json"]["laws"]}["N0030001"]
    prov = json.loads((ROOT / "bronze/N0030001/provenance.json").read_text(encoding="utf-8"))
    for k in ("law_name", "law_url", "last_amended", "mirror_commit", "fetched_at", "sha256"):
        assert law[k] == prov[k]
    assert built["manifest.json"]["sources"]["bronze/N0030001/source.json"] == prov["sha256"]   # 快照本身與 provenance 核對過
    inv = law["inventory"]
    assert inv["units"] == sum(inv["by_category"].values()) > 0
    assert {u["unit_id"] for u in inv["encoded_units"]} >= {"N0030001/24/1", "N0030001/38/1"}


def test_family_entries_carry_version_hashes_sign_off_rules_and_golden_status(built):
    import family_reviews as fr
    fams = {f["id"]: f for f in built["families.json"]["families"]}
    assert set(fams) == {"overtime_pay", "annual_leave_days"}
    for fid, f in fams.items():
        fdir = ROOT / "silver/families" / fid
        assert f["hashes"] == fr.family_hashes(fdir)
        assert f["status"] == fr.family_status(fdir)["status"]
        assert f["reviews"] and f["reviews"][0]["reviewer"] == "Calvin Ku"
        assert f["legal_basis"] and all(b["encodes"] for b in f["legal_basis"])
        assert f["formulas"] and f["golden"]
        assert sum(f["golden_status_counts"].values()) == len(f["golden"])
        pub = f["public_set"]
        assert pub["path"] == f"gold/releases/{RELEASE}/public/{fid}.jsonl" and len(pub["sha256"]) == 64
        assert pub["matches_current_files"] is True                   # 題集的四個 hash = 目前的 family 檔
        assert f["private_set"] is None                                # 私有題集還沒封存
    ot = fams["overtime_pay"]
    assert all(isinstance(e, dict) and e["reason"] for e in ot["scope"]["excludes"])
    assert {b["id"]: b["ai_review"]["verdict"] for b in ot["legal_basis"]}["MOL-RESTDAY-9H"] == "warn"


def test_every_run_points_to_its_log_with_a_matching_hash(built):
    runs = [r for t in built["results.json"]["targets"] for s in t["splits"].values() for r in s["runs"]]
    assert runs
    for r in runs:
        assert _log_hash(r["log_path"]) == r["log_sha256"]
    luna = {t["target"]: t for t in built["results.json"]["targets"]}["gpt-6-luna@api@opencode@max"]
    assert luna["model"] == "gpt-6-luna" and luna["answer_mode"] == "max" and luna["is_fake"] is False
    # 2026Q4 的 release 只收當期題集上的 run（舊版題集的 log 在 eval/logs/2026Q4-superseded/）
    assert [(r["stale"], r["stale_fields"]) for r in luna["splits"]["public"]["runs"]] == [(False, []), (False, [])]


def test_results_copy_the_index_scores_without_recomputing(built):
    ix = json.loads((ROOT / f"index/{RELEASE}/index.json").read_text(encoding="utf-8"))
    res = built["results.json"]
    assert res["mode"] == ix["mode"] and res["release_blockers"] == ix["release_blockers"]
    for t in res["targets"]:
        src = ix["targets"][t["target"]]["splits"]
        for split, s in t["splits"].items():
            assert s["score"] == src[split]["score"] and s["family_scores"] == src[split]["family_scores"]


def test_nothing_private_leaks(built, monkeypatch):
    blob = "".join((built["_dir"] / f).read_text(encoding="utf-8") for f in FILES)
    assert "/private/" not in blob
    salt = os.environ.get("TAIWANEVAL_PRIVATE_SALT")
    if salt:
        assert salt not in blob


def test_output_is_deterministic(tmp_path, built):
    sd.build(ROOT, RELEASE, tmp_path)
    for f in FILES:
        assert (tmp_path / f).read_bytes() == (built["_dir"] / f).read_bytes()


def test_check_passes_on_the_committed_bundle():
    assert sd.check(ROOT, RELEASE) == []


def test_check_fails_when_a_source_changes_after_the_bundle_was_built(tmp_path):
    repo = tmp_path / "repo"
    for d in ("bronze", "silver", "gold", "index", "tools"):
        shutil.copytree(ROOT / d, repo / d, ignore=shutil.ignore_patterns("__pycache__", "prompts", "private"))
    shutil.copy(ROOT / "datasets.yaml", repo / "datasets.yaml")
    _link_eval(repo)
    assert sd.check(repo, RELEASE) == []
    spec = repo / "silver/families/annual_leave_days/spec.yaml"
    spec.write_text(spec.read_text(encoding="utf-8").replace("特別休假日數", "特別休假天數", 1), encoding="utf-8")
    problems = sd.check(repo, RELEASE)
    assert problems and any("families.json" in p for p in problems)


@pytest.mark.skipif(not HAS_LOGS, reason="公開版不帶原始評測紀錄；得分表的同一項檢查見下一個測試")
def test_a_log_whose_bytes_differ_from_the_index_hash_is_refused(tmp_path):
    repo = tmp_path / "repo"
    for d in ("bronze", "silver", "gold", "index"):
        shutil.copytree(ROOT / d, repo / d, ignore=shutil.ignore_patterns("__pycache__", "prompts", "private"))
    shutil.copy(ROOT / "datasets.yaml", repo / "datasets.yaml")
    shutil.copytree(ROOT / "eval/logs", repo / "eval/logs")
    ix = json.loads((repo / f"index/{RELEASE}/index.json").read_text(encoding="utf-8"))
    used = sorted(r["log_file"] for t in ix["targets"].values() for s in t["splits"].values() for r in s.get("families", {}).values())
    victim = repo / "eval/logs" / RELEASE / used[0]
    victim.write_bytes(victim.read_bytes() + b"x")
    with pytest.raises(ValueError, match="log_sha256"):
        sd.build(repo, RELEASE, tmp_path / "out")


# ---- 沒有原始評測紀錄時（公開版）：log 的 sha256 取自逐題得分表

def _scores_only_copy(tmp_path):
    repo = tmp_path / "repo"
    for d in ("bronze", "silver", "gold", "index"):
        shutil.copytree(ROOT / d, repo / d, ignore=shutil.ignore_patterns("__pycache__", "prompts", "private"))
    shutil.copy(ROOT / "datasets.yaml", repo / "datasets.yaml")
    shutil.copytree(ROOT / "eval/scores", repo / "eval/scores")
    return repo


def test_without_the_logs_the_bundle_is_built_from_the_score_table_and_is_byte_identical(tmp_path, built):
    repo = _scores_only_copy(tmp_path)
    assert not (repo / "eval/logs").exists()
    sd.build(repo, RELEASE, tmp_path / "out")
    for f in FILES:
        assert (tmp_path / "out" / f).read_bytes() == (built["_dir"] / f).read_bytes(), f


def test_a_score_table_whose_log_hash_differs_from_the_index_is_refused(tmp_path):
    repo = _scores_only_copy(tmp_path)
    p = repo / f"eval/scores/{RELEASE}/runs.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    for r in doc["runs"]:
        r["log_sha256"] = "ab" * 32
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="log_sha256"):
        sd.build(repo, RELEASE, tmp_path / "out")


def test_a_log_that_is_neither_in_the_repo_nor_in_the_score_table_is_refused(tmp_path):
    repo = _scores_only_copy(tmp_path)
    shutil.rmtree(repo / "eval/scores")
    with pytest.raises(ValueError, match="不存在"):
        sd.build(repo, RELEASE, tmp_path / "out")


# ---- 審查後補的案例（PR 前 review）

def _repo_copy(tmp_path):
    repo = tmp_path / "repo"
    for d in ("bronze", "silver", "gold", "index"):
        shutil.copytree(ROOT / d, repo / d, ignore=shutil.ignore_patterns("__pycache__", "prompts", "private"))
    shutil.copy(ROOT / "datasets.yaml", repo / "datasets.yaml")
    _link_eval(repo)
    return repo


def _families(repo):
    sd.build(repo, RELEASE, repo / "out")
    return {f["id"]: f for f in json.loads((repo / "out/families.json").read_text(encoding="utf-8"))["families"]}


def test_every_spec_key_is_exported_or_deliberately_omitted():
    import yaml
    for fid in ("overtime_pay", "annual_leave_days"):
        spec = yaml.safe_load((ROOT / "silver/families" / fid / "spec.yaml").read_text(encoding="utf-8"))
        assert set(spec) <= set(sd.SPEC_EXPORTED) | set(sd.SPEC_OMITTED), set(spec) - set(sd.SPEC_EXPORTED) - set(sd.SPEC_OMITTED)


def test_rule_card_fields_survive_formula_mathml_verify_and_branch_details(tmp_path):
    repo = _repo_copy(tmp_path)
    spec = repo / "silver/families/overtime_pay/spec.yaml"
    spec.write_text(spec.read_text(encoding="utf-8").replace("  - id: LSA-24-1\n", "  - id: LSA-24-1\n    verify: pending\n", 1), encoding="utf-8")
    fams = _families(repo)
    ot, al = fams["overtime_pay"], fams["annual_leave_days"]
    assert all(f.get("mathml") for f in ot["formulas"])                     # 加班費的公式只有 mathml，不能丟
    assert {b["id"]: b.get("verify") for b in ot["legal_basis"]}["LSA-24-1"] == "pending"   # 待核對不能被洗成已核對
    assert {b["id"]: b for b in ot["branches"]}["R3"]["day_type"] == "rest_day"
    assert al["formula_legend"] and al["boundary_values"] and al["answer"]


def test_a_run_without_a_manifest_has_expected_n_null_and_still_builds(tmp_path):
    repo = _repo_copy(tmp_path)
    ij = repo / f"index/{RELEASE}/index.json"
    ix = json.loads(ij.read_text(encoding="utf-8"))
    run = ix["targets"]["fake-oracle@none@none@direct"]["splits"]["public"]["families"]["overtime_pay"]
    run.update(expected_n=None, complete=False, accuracy=None, control_pass=None)
    ij.write_text(json.dumps(ix, ensure_ascii=False, indent=1), encoding="utf-8")
    sd.build(repo, RELEASE, repo / "out")
    res = json.loads((repo / "out/results.json").read_text(encoding="utf-8"))
    r = {t["target"]: t for t in res["targets"]}["fake-oracle@none@none@direct"]["splits"]["public"]["runs"]
    assert {x["family"]: x for x in r}["overtime_pay"]["expected_n"] is None


def test_a_run_on_an_older_question_set_is_marked_stale_with_the_fields_that_differ(tmp_path):
    """舊版題集跑的 run（index 的 spec_mismatch 非 null）網站要標出來。"""
    repo = _repo_copy(tmp_path)
    ij = repo / f"index/{RELEASE}/index.json"
    ix = json.loads(ij.read_text(encoding="utf-8"))
    run = ix["targets"]["fake-oracle@none@none@direct"]["splits"]["public"]["families"]["overtime_pay"]
    run["spec_mismatch"] = {"log_has": [], "manifest": {}, "differs": ["spec_sha256", "generator_version"]}
    ij.write_text(json.dumps(ix, ensure_ascii=False, indent=1), encoding="utf-8")
    sd.build(repo, RELEASE, repo / "out")
    res = json.loads((repo / "out/results.json").read_text(encoding="utf-8"))
    runs = {x["family"]: x for x in {t["target"]: t for t in res["targets"]}["fake-oracle@none@none@direct"]["splits"]["public"]["runs"]}
    assert runs["overtime_pay"]["stale"] is True and runs["overtime_pay"]["stale_fields"] == ["spec_sha256", "generator_version"]
    assert runs["annual_leave_days"]["stale"] is False and runs["annual_leave_days"]["stale_fields"] == []


def test_private_split_logs_are_never_opened_and_their_path_is_withheld(tmp_path):
    repo = _repo_copy(tmp_path)
    ij = repo / f"index/{RELEASE}/index.json"
    ix = json.loads(ij.read_text(encoding="utf-8"))
    t = ix["targets"]["fake-oracle@none@none@direct"]["splits"]
    priv = json.loads(json.dumps(t["public"]))
    for r in priv["families"].values():
        r.update(log_file="2026-12-01T00-00-00-00-00_private-not-in-repo.eval", log_sha256="ab" * 32)
    t["private"] = priv
    ij.write_text(json.dumps(ix, ensure_ascii=False, indent=1), encoding="utf-8")
    sd.build(repo, RELEASE, repo / "out")
    res = json.loads((repo / "out/results.json").read_text(encoding="utf-8"))
    runs = {t["target"]: t for t in res["targets"]}["fake-oracle@none@none@direct"]["splits"]["private"]["runs"]
    assert runs and all(r["log_path"] is None and r["log_withheld"] and r["log_sha256"] == "ab" * 32 for r in runs)
    m = json.loads((repo / "out/manifest.json").read_text(encoding="utf-8"))
    assert not any("private" in s for s in m["sources"])


def test_unquoted_yaml_dates_become_iso_strings(tmp_path):
    repo = _repo_copy(tmp_path)
    spec = repo / "silver/families/annual_leave_days/spec.yaml"
    spec.write_text(spec.read_text(encoding="utf-8").replace('date: "2026-10-12"', "date: 2026-10-12", 1), encoding="utf-8")
    al = _families(repo)["annual_leave_days"]
    assert al["changelog"][0]["date"] == "2026-10-12"


def test_check_reports_instead_of_crashing_when_an_input_is_missing(tmp_path):
    repo = _repo_copy(tmp_path)
    (repo / f"index/{RELEASE}/index.json").unlink()
    problems = sd.check(repo, RELEASE)
    assert problems and f"index/{RELEASE}/index.json" in problems[0]


def test_an_inventory_without_triage_is_an_error_not_silently_null(tmp_path):
    repo = _repo_copy(tmp_path)
    (repo / "silver/inventory/N0030001/triage.yaml").unlink()
    with pytest.raises(ValueError, match="triage.yaml"):
        sd.build(repo, RELEASE, repo / "out")


def test_manifest_sources_include_the_public_logs_that_were_hashed(built):
    runs = [r for t in built["results.json"]["targets"] for s in t["splits"].values() for r in s["runs"] if r["log_path"]]
    assert {r["log_path"] for r in runs} <= set(built["manifest.json"]["sources"])


# ---- 依 release 的 lineage 輸出來源

def _bundle(repo, out):
    sd.build(repo, RELEASE, out)
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def _fake_notice(repo, sid="test-site-data-fixture"):
    # 名稱刻意與任何真實或其他測試用的來源不同：測試在 repo 的複本上加目錄，不能撞到已提交的快照
    import hashlib
    data = b"%PDF-1.4 fake notice for site-data"
    d = repo / "bronze" / sid
    d.mkdir(parents=True)
    (d / "source.pdf").write_bytes(data)
    prov = {"source_type": "announcement", "source_id": sid, "title": "測試公告", "publisher": "測試部",
            "url": "https://example.gov.tw/n", "retrieved_url": "https://example.gov.tw/n.pdf", "published_on": "2024-05-01",
            "document_number": None, "version": None, "source_file": "source.pdf", "media_type": "application/pdf",
            "sha256": hashlib.sha256(data).hexdigest(), "fetched_at": "2026-11-13T10:00:00+08:00",
            "fetched_by": "tools/fetch_source.py 0.1.0", "license": "unknown", "note": "測試用假來源"}
    (d / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False), encoding="utf-8")
    return prov


def test_a_bronze_snapshot_the_release_does_not_use_leaves_its_site_data_unchanged(tmp_path):
    """新增 bronze 快照（法規或非法規）不改變沒用到它的 release：laws() 與 sources() 只讀 manifest 的 lineage。"""
    repo = _repo_copy(tmp_path)
    before = _bundle(repo, tmp_path / "before")
    _fake_notice(repo)
    shutil.copytree(repo / "bronze/N0030001", repo / "bronze/X9999999")       # 一部沒有 family 用到的「法規」
    assert _bundle(repo, tmp_path / "after") == before


def test_laws_come_from_the_release_lineage_and_keep_their_format(built):
    pcodes = sorted({p for f in ("overtime_pay", "annual_leave_days")
                     for p in json.loads((ROOT / f"gold/releases/{RELEASE}/public/{f}.manifest.json").read_text(encoding="utf-8"))["lineage"]["laws"]})
    assert [x["pcode"] for x in built["laws.json"]["laws"]] == pcodes == ["N0030001"]
    assert built["sources.json"] == {"sources": []}                            # 2026Q4 沒有非法規來源
    assert built["manifest.json"]["files"]["sources.json"]


def _use_fake_notice(repo, fid="annual_leave_days"):
    prov = _fake_notice(repo)
    mp = repo / f"gold/releases/{RELEASE}/public/{fid}.manifest.json"
    man = json.loads(mp.read_text(encoding="utf-8"))
    man["lineage"]["sources"] = {prov["source_id"]: {k: prov[k] for k in ("source_type", "title", "publisher", "published_on",
                                                                          "document_number", "version")}
                                 | {"bronze_sha256": prov["sha256"], "fetched_at": prov["fetched_at"]}}
    mp.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return prov


def test_sources_list_the_non_law_sources_a_release_used_with_their_provenance(tmp_path):
    repo = _repo_copy(tmp_path)
    prov = _use_fake_notice(repo)
    sd.build(repo, RELEASE, repo / "out")
    srcs = json.loads((repo / "out/sources.json").read_text(encoding="utf-8"))["sources"]
    assert [s["source_id"] for s in srcs] == ["test-site-data-fixture"]
    s = srcs[0]
    assert {k: s[k] for k in prov} == prov and s["families"] == ["annual_leave_days"]
    m = json.loads((repo / "out/manifest.json").read_text(encoding="utf-8"))
    assert m["sources"]["bronze/test-site-data-fixture/source.pdf"] == prov["sha256"]
    laws = json.loads((repo / "out/laws.json").read_text(encoding="utf-8"))["laws"]
    assert [x["pcode"] for x in laws] == ["N0030001"]                          # 非法規來源不會被當成法規


def test_a_source_whose_bronze_hash_differs_from_the_lineage_is_refused(tmp_path):
    repo = _repo_copy(tmp_path)
    _use_fake_notice(repo)
    (repo / "bronze/test-site-data-fixture/source.pdf").write_bytes(b"edited")
    with pytest.raises(ValueError, match="sha256"):
        sd.build(repo, RELEASE, repo / "out")


def test_a_lineage_entry_without_a_bronze_snapshot_is_refused(tmp_path):
    repo = _repo_copy(tmp_path)
    _use_fake_notice(repo)
    shutil.rmtree(repo / "bronze/test-site-data-fixture")
    with pytest.raises(ValueError, match="test-site-data-fixture"):
        sd.build(repo, RELEASE, repo / "out")


def test_a_spec_with_bronze_sources_builds_and_the_sources_come_from_the_lineage(tmp_path):
    """bronze_sources 是 spec 的欄位，但網站讀的來源以題集 lineage 為準（sources.json），families.json 不重複列。"""
    repo = _repo_copy(tmp_path)
    spec = repo / "silver/families/annual_leave_days/spec.yaml"
    spec.write_text(spec.read_text(encoding="utf-8") + "bronze_sources:\n  - test-site-data-fixture\n", encoding="utf-8")
    assert "bronze_sources" in sd.SPEC_OMITTED
    al = _families(repo)["annual_leave_days"]
    assert "bronze_sources" not in al


def test_a_law_without_an_inventory_in_the_lineage_is_listed_with_inventory_null(tmp_path):
    """bronze_sources 引用、沒有 silver/inventory 的法規（例如勞工退休金條例）照樣列進 laws.json，inventory 是 null。"""
    repo = _repo_copy(tmp_path)
    shutil.copytree(repo / "bronze/N0030001", repo / "bronze/X9000001")   # 同一份 bytes：sha256 與 provenance 相符
    prov = json.loads((repo / "bronze/X9000001/provenance.json").read_text(encoding="utf-8"))
    mp = repo / f"gold/releases/{RELEASE}/public/annual_leave_days.manifest.json"
    man = json.loads(mp.read_text(encoding="utf-8"))
    man["lineage"]["laws"]["X9000001"] = {k: prov[k] for k in ("law_name", "last_amended", "mirror_commit")} | {"bronze_sha256": prov["sha256"]}
    mp.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    sd.build(repo, RELEASE, repo / "out")
    laws = {x["pcode"]: x for x in json.loads((repo / "out/laws.json").read_text(encoding="utf-8"))["laws"]}
    assert list(laws) == ["N0030001", "X9000001"]
    assert laws["X9000001"]["inventory"] is None and laws["N0030001"]["inventory"] is not None
