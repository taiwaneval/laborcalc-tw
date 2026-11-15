"""tools/reproduce.py 的比對邏輯。

完整流程（clone、只用 requirements.txt 裝依賴、逐層重跑）由 make reproduce 實際執行；這裡測不需要網路與 venv 的部分：
bronze 核對、用 git 找出各層的差異、各層重跑前先清掉產物、index.json 只忽略 generated_at、執行環境的隔離。
各層的工具在這裡換成小的替身腳本，只驗證 reproduce 怎麼重跑與比對，不驗證工具本身。
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("reproduce_under_test", ROOT / "tools/reproduce.py")
rp = importlib.util.module_from_spec(_s); _s.loader.exec_module(rp)

GIT = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def git(r: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(r), *GIT, *args], check=True, capture_output=True)


def git_repo(tmp_path: Path, files: dict[str, str], name: str = "repo") -> Path:
    r = tmp_path / name
    for rel, text in files.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    git(r.parent, "init", "-q", name) if not (r / ".git").exists() else None
    git(r, "add", "-f", ".")
    git(r, "commit", "-qm", "c")
    return r


def clone_of(r: Path) -> "rp.Clone":
    return rp.Clone(r, Path(sys.executable))


LAW = {"法規名稱": "測試法", "法規網址": "https://law.moj.gov.tw/x", "最新異動日期": "20240731", "法規內容": []}


def bronze(root: Path, recorded: bytes | None = None, **prov) -> Path:
    data = json.dumps(LAW, ensure_ascii=False).encode()
    d = root / "bronze" / "X0000001"
    d.mkdir(parents=True)
    (d / "source.json").write_bytes(data)
    meta = {"sha256": hashlib.sha256(recorded or data).hexdigest(),
            "law_name": LAW["法規名稱"], "law_url": LAW["法規網址"], "last_amended": LAW["最新異動日期"]}
    (d / "provenance.json").write_text(json.dumps({**meta, **prov}, ensure_ascii=False))
    return root


MIRROR = {"source": "kong0107/mojLawSplitJSON (mirror of law.moj.gov.tw open data)", "mirror_commit": "abc123",
          "source_file": "FalVMingLing/X0000001.json"}


# ---- bronze
def test_bronze_passes_when_the_snapshot_matches_its_provenance(tmp_path):
    assert rp.check_bronze(bronze(tmp_path)) == []


def test_bronze_failure_names_the_layer_and_the_file(tmp_path):
    fails = rp.check_bronze(bronze(tmp_path, recorded=b"something else"))
    assert [(f.layer, f.path) for f in fails] == [("bronze", "bronze/X0000001/source.json")]


def test_bronze_without_provenance_fails(tmp_path):
    (tmp_path / "bronze/X0000001").mkdir(parents=True)
    (tmp_path / "bronze/X0000001/source.json").write_text("{}")
    assert [f.layer for f in rp.check_bronze(tmp_path)] == ["bronze"]


def test_provenance_metadata_must_match_the_snapshot(tmp_path):
    """law_name、law_url、last_amended 會進每題的 lineage；手改 provenance 也要被抓到。"""
    fails = rp.check_bronze(bronze(tmp_path, last_amended="20990101"))
    assert [(f.layer, f.path) for f in fails] == [("bronze", "bronze/X0000001/provenance.json")]
    assert "last_amended" in fails[0].detail


# ---- 非法規來源（docs/bronze-non-law-sources.md）：依 provenance 的 source_type 分流
NOTICE = b"%PDF-1.4 fake notice"


def non_law(root: Path, sid: str = "test-fake-notice", data: bytes = NOTICE, **over) -> Path:
    d = root / "bronze" / sid
    d.mkdir(parents=True)
    (d / "source.pdf").write_bytes(data)
    prov = {"source_type": "announcement", "source_id": sid, "title": "測試公告", "publisher": "測試部",
            "url": "https://example.gov.tw/n", "retrieved_url": "https://example.gov.tw/n.pdf", "published_on": "2024-05-01",
            "document_number": None, "version": None, "source_file": "source.pdf", "media_type": "application/pdf",
            "sha256": hashlib.sha256(data).hexdigest(), "fetched_at": "2026-11-13T10:00:00+08:00",
            "fetched_by": "tools/fetch_source.py 0.1.0", "license": "unknown", "note": "測試用假來源", **over}
    (d / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False), encoding="utf-8")
    return root


def test_a_non_law_source_next_to_a_law_passes_without_a_source_json(tmp_path):
    root = non_law(bronze(tmp_path))
    assert rp.check_bronze(root) == []
    done = rp.bronze_done(root, online=True)
    assert any("test-fake-notice/source.pdf" in d and "announcement" in d for d in done)
    assert not any("test-fake-notice" in d and ("law_name" in d or "鏡像" in d) for d in done)


def test_a_non_law_source_whose_bytes_changed_fails_on_that_file(tmp_path):
    root = non_law(tmp_path)
    (root / "bronze/test-fake-notice/source.pdf").write_bytes(NOTICE + b"edited")
    fails = rp.check_bronze(root)
    assert [(f.layer, f.path) for f in fails] == [("bronze", "bronze/test-fake-notice/provenance.json")]
    assert "sha256" in fails[0].detail


@pytest.mark.parametrize("over, words", [
    ({"source_type": "calculator"}, "calculator"),
    ({"source_id": "other-id"}, "source_id"),
    ({"published_on": None}, "published_on"),
])
def test_non_law_provenance_rules_are_enforced(tmp_path, over, words):
    fails = rp.check_bronze(non_law(tmp_path, **over))
    assert [f.layer for f in fails] == ["bronze"] and words in fails[0].detail


def test_a_non_law_provenance_missing_a_field_fails(tmp_path):
    root = non_law(tmp_path)
    p = root / "bronze/test-fake-notice/provenance.json"
    prov = json.loads(p.read_text(encoding="utf-8")); del prov["license"]
    p.write_text(json.dumps(prov, ensure_ascii=False), encoding="utf-8")
    assert "license" in rp.check_bronze(root)[0].detail


def test_a_non_law_source_is_never_checked_against_the_law_mirror(tmp_path):
    root = non_law(tmp_path)
    assert rp.check_bronze_online(root, fetch=lambda url: pytest.fail(f"不該下載 {url}")) == []


def test_online_check_downloads_the_file_at_the_recorded_mirror_commit(tmp_path):
    root = bronze(tmp_path, **MIRROR)
    data = (root / "bronze/X0000001/source.json").read_bytes()
    seen = []
    assert rp.check_bronze_online(root, fetch=lambda url: seen.append(url) or data) == []
    assert seen == ["https://raw.githubusercontent.com/kong0107/mojLawSplitJSON/abc123/FalVMingLing/X0000001.json"]
    assert [f.layer for f in rp.check_bronze_online(root, fetch=lambda url: b"changed upstream")] == ["bronze"]


def test_a_failed_download_is_reported_as_a_bronze_failure_not_a_crash(tmp_path):
    root = bronze(tmp_path, **MIRROR)
    def offline(url):
        raise OSError("certificate verify failed")
    fails = rp.check_bronze_online(root, fetch=offline)
    assert [f.layer for f in fails] == ["bronze"] and "certificate verify failed" in fails[0].detail


def test_the_report_only_claims_a_mirror_check_for_snapshots_that_have_a_mirror(tmp_path):
    root = bronze(tmp_path)
    assert "鏡像" not in " ".join(rp.bronze_done(root, online=True))
    assert "鏡像" in " ".join(rp.bronze_done(bronze(tmp_path / "m", **MIRROR), online=True))


def test_downloads_use_the_venv_python_and_its_certifi(tmp_path):
    """系統 Python 不一定設好 CA 憑證（python.org 的 macOS 版要另跑 Install Certificates）；
    下載改由只裝 requirements.txt 的暫存 venv 執行，用其中的 certifi。"""
    page = tmp_path / "page.txt"
    page.write_bytes(b"mirror bytes")
    assert rp.downloader(Path(sys.executable))(page.as_uri()) == b"mirror bytes"
    with pytest.raises(OSError, match="missing.txt"):
        rp.downloader(Path(sys.executable))((tmp_path / "missing.txt").as_uri())


# ---- 執行環境：clone 裡的檢查不受開發者環境影響，也碰不到真正的 repo
def test_git_variables_exported_to_hooks_are_dropped_before_any_git_call(tmp_path, monkeypatch):
    """在 git hook 裡執行時，GIT_DIR、GIT_INDEX_FILE 指向真正的 repo；沿用的話，對暫存 clone 下的 checkout 會改到真正的 repo。"""
    r = git_repo(tmp_path, {"a.txt": "a\n"})
    other = git_repo(tmp_path, {"b.txt": "b\n"}, name="other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    rp.scrub_git_env()
    assert "GIT_DIR" not in os.environ and "GIT_INDEX_FILE" not in os.environ
    assert Path(rp.git(r, "rev-parse", "--absolute-git-dir").strip()).resolve() == (r / ".git").resolve()


def test_clone_env_keeps_only_what_the_tools_need():
    base = {"PATH": "/bin", "HOME": "/h", "LANG": "zh_TW.UTF-8", "HTTPS_PROXY": "http://p", "PYTEST_ADDOPTS": "-k x",
            "TAIWANEVAL_PRIVATE_SALT": "s", "PIP_INDEX_URL": "u", "NODE_OPTIONS": "--x", "GIT_DIR": "/r/.git"}
    assert rp.clone_env(base) == {"PATH": "/bin", "HOME": "/h", "LANG": "zh_TW.UTF-8", "HTTPS_PROXY": "http://p"}


def test_tools_in_the_clone_do_not_see_the_developers_pytest_salt_or_git_settings(tmp_path, monkeypatch):
    """PYTEST_ADDOPTS（例如 -k、-m "not slow"）能讓 tests 層只跑一部分卻照樣全綠。"""
    probe = ("import os, sys\n"
             "leaked = [k for k in ('PYTEST_ADDOPTS', 'TAIWANEVAL_PRIVATE_SALT', 'GIT_DIR') if k in os.environ]\n"
             "sys.exit(f'leaked {leaked}' if leaked else (0 if sys.flags.utf8_mode else 'not utf8 mode'))\n")
    r = git_repo(tmp_path, {"probe.py": probe})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("TAIWANEVAL_PRIVATE_SALT", "secret")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere/.git"))
    assert clone_of(r).run("tests", ["probe.py"]) is None


def test_install_uses_only_the_lock_and_checks_it_is_complete():
    install, check = rp.install_commands(Path("/v/bin/python"), Path("/c/requirements.txt"))
    assert install[:5] == ["/v/bin/python", "-E", "-s", "-X", "utf8"] and "--no-deps" in install
    assert install[-2:] == ["-r", "/c/requirements.txt"]
    assert check == ["/v/bin/python", "-E", "-s", "-X", "utf8", "-m", "pip", "check"]


def test_a_branch_name_resolves_to_its_commit_in_the_clone(tmp_path):
    src = git_repo(tmp_path, {"a.txt": "a\n"})
    default = rp.git(src, "branch", "--show-current").strip()
    git(src, "checkout", "-qb", "other")
    (src / "a.txt").write_text("b\n")
    git(src, "commit", "-qam", "other")
    want = rp.git(src, "rev-parse", "other").strip()
    git(src, "checkout", "-q", default)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True)
    assert rp.resolve_rev(clone, "other") == want          # clone 裡只有 origin/other
    assert rp.resolve_rev(clone, want[:10]) == want
    with pytest.raises(SystemExit, match="no-such"):
        rp.resolve_rev(clone, "no-such")


def test_uncommitted_changes_are_named_in_the_note(tmp_path):
    """只檢查已提交的內容；工作目錄有改動時，開頭與結尾都要說，免得把舊 commit 的全綠當成這次改動的結果。"""
    r = git_repo(tmp_path, {"a.txt": "a\n"})
    assert rp.dirty_note(r, "abc") is None
    (r / "a.txt").write_text("b\n")
    (r / "new.txt").write_text("n\n")
    assert "2 個未提交的改動" in rp.dirty_note(r, "abc")


# ---- 用 git 找差異
def test_changed_files_stay_within_the_layer_and_include_new_and_deleted_files(tmp_path):
    r = git_repo(tmp_path, {"gold/a.jsonl": "1\n", "gold/b.jsonl": "2\n", "index/i.json": "{}\n"})
    (r / "gold/a.jsonl").write_text("changed\n")
    (r / "gold/b.jsonl").unlink()
    (r / "gold/new.jsonl").write_text("x\n")
    (r / "index/i.json").write_text("{1}\n")
    assert sorted(p for _, p in rp.changed_files(r, ["gold"])) == ["gold/a.jsonl", "gold/b.jsonl", "gold/new.jsonl"]


def test_first_diff_shows_the_changed_line(tmp_path):
    r = git_repo(tmp_path, {"gold/a.jsonl": "line1\nline2\n"})
    (r / "gold/a.jsonl").write_text("line1\nLINE2\n")
    d = rp.first_diff(r, "gold/a.jsonl")
    assert "-line2" in d and "+LINE2" in d
    assert d.splitlines()[0] == "（- 已提交　+ 重建）"


def test_first_diff_of_a_long_line_shows_where_it_differs(tmp_path):
    """JSONL 一題一行、很長；只截前 160 字時，- 與 + 兩行看起來一樣。視窗要對準第一個不同處。"""
    a = '{"item_id": "x", "prompt": "' + "題" * 300 + '", "gold": 1200}\n'
    r = git_repo(tmp_path, {"gold/a.jsonl": a})
    (r / "gold/a.jsonl").write_text(a.replace('"gold": 1200', '"gold": 1201'))
    d = rp.first_diff(r, "gold/a.jsonl")
    assert '"gold": 1200' in d and '"gold": 1201' in d


def test_restore_puts_the_layer_back_to_the_committed_state(tmp_path):
    """每層比對完就還原，下一層從已提交的輸入重跑：失敗只指向出問題的那一層。"""
    r = git_repo(tmp_path, {"gold/a.jsonl": "1\n", "index/i.json": "{}\n"})
    (r / "gold/a.jsonl").unlink()
    (r / "gold/new.jsonl").write_text("x\n")
    (r / "index/i.json").write_text("{1}\n")
    rp.restore(r, ["gold"])
    assert rp.changed_files(r, ["gold"]) == [] and rp.changed_files(r, ["index"]) != []


def test_restore_tolerates_a_path_that_only_exists_as_new_files(tmp_path):
    r = git_repo(tmp_path, {"silver/inventory/X/inventory.json": "[]\n"})
    (r / "silver/inventory/X/prompts").mkdir()
    (r / "silver/inventory/X/prompts/batch_01.txt").write_text("新\n")
    rp.restore(r, ["silver/inventory/X/inventory.json", "silver/inventory/X/prompts"])
    assert rp.changed_files(r, []) == []


# ---- silver：重跑前先清掉產物，不再產生的檔會顯示為被刪除
BUILD_STUB = 'import pathlib, sys\n(pathlib.Path("silver/inventory") / sys.argv[1] / "inventory.json").write_text("[]\\n")\n'
TRIAGE_STUB = ('import pathlib, sys\np = pathlib.Path("silver/inventory") / sys.argv[1] / "prompts"\n'
               'p.mkdir(exist_ok=True)\n(p / "batch_01.txt").write_text("b1\\n")\n')


def test_a_prompt_batch_the_pipeline_no_longer_writes_is_reported(tmp_path):
    r = git_repo(tmp_path, {"tools/build_inventory.py": BUILD_STUB, "tools/triage.py": TRIAGE_STUB,
                            "silver/inventory/X/inventory.json": "[]\n", "silver/inventory/X/prompts/batch_01.txt": "b1\n",
                            "silver/inventory/X/prompts/batch_02.txt": "條文變短後不會再產生的批次\n"})
    fails, _ = rp.layer_silver(clone_of(r))
    assert [(f.layer, f.path, f.detail) for f in fails] == [("silver", "silver/inventory/X/prompts/batch_02.txt", "檔案被刪除")]
    assert rp.changed_files(r, []) == []


# ---- gold：重生到暫存目錄，整個 public 目錄要等於重生的結果
GEN_STUB = """import pathlib, sys
a = sys.argv
out, fid = pathlib.Path(a[a.index("--out-dir") + 1]), a[1]
out.mkdir(parents=True, exist_ok=True)
(out / f"{fid}.jsonl").write_text("regenerated\\n")
for ext in ("manifest.json", "coverage.json"):
    (out / f"{fid}.{ext}").write_text(pathlib.Path(f"gold/releases/R/public/{fid}.{ext}").read_text())
"""
MANIFEST = {"family": "f", "release": "R", "split": "public", "n": 1, "rejected": 0, "as_of": "2026-10-08"}


def gold_repo(tmp_path: Path, extra: dict[str, str] | None = None, stub: str = GEN_STUB, manifest: dict = MANIFEST) -> Path:
    return git_repo(tmp_path, {"gold/generate.py": stub, "gold/releases/R/public/f.jsonl": "regenerated\n",
                               "gold/releases/R/public/f.manifest.json": json.dumps(manifest),
                               "gold/releases/R/public/f.coverage.json": "{}", **(extra or {})})


def test_a_gold_mismatch_names_the_committed_file_and_shows_the_diff(tmp_path):
    """已提交的題集被改過時，要指出哪個檔、差在哪；不能只回報產生器的版本守門訊息（那會誤導成「要 bump 版本」）。"""
    r = gold_repo(tmp_path, {"gold/releases/R/public/f.jsonl": "committed\n"})
    fails, _ = rp.layer_gold(clone_of(r), "R")
    assert [(f.layer, f.path) for f in fails] == [("gold", "gold/releases/R/public/f.jsonl")]
    assert "-committed" in fails[0].detail and "+regenerated" in fails[0].detail
    assert rp.changed_files(r, []) == []


def test_a_file_the_generator_no_longer_writes_is_reported_as_deleted(tmp_path):
    r = gold_repo(tmp_path, stub=GEN_STUB.replace('("manifest.json", "coverage.json")', '("manifest.json",)'))
    fails, _ = rp.layer_gold(clone_of(r), "R")
    assert [(f.path, f.detail) for f in fails] == [("gold/releases/R/public/f.coverage.json", "檔案被刪除")]


def test_a_public_file_without_a_manifest_is_reported(tmp_path):
    r = gold_repo(tmp_path, {"gold/releases/R/public/g.jsonl": "沒有 manifest，重生不出來\n"})
    fails, _ = rp.layer_gold(clone_of(r), "R")
    assert [(f.path, f.detail) for f in fails] == [("gold/releases/R/public/g.jsonl", "檔案被刪除")]


def test_a_manifest_that_does_not_match_its_place_is_reported(tmp_path):
    """另一個 release 的 manifest 被放進這裡時，照它記的值重生會「一致」；位置與內容要對得上。"""
    r = gold_repo(tmp_path, manifest={**MANIFEST, "release": "Q"})
    fails, _ = rp.layer_gold(clone_of(r), "R")
    assert [f.path for f in fails] == ["gold/releases/R/public/f.manifest.json"] and "release" in fails[0].detail


def test_a_committed_private_file_is_reported(tmp_path):
    r = gold_repo(tmp_path, {"gold/releases/R/private/f.jsonl": "private 題目\n"})
    fails, _ = rp.layer_gold(clone_of(r), "R")
    assert [(f.layer, f.path) for f in fails] == [("gold", "gold/releases/R/private/f.jsonl")]


# ---- index：重跑前先清掉，只忽略 generated_at
AGG_STUB = """import json, pathlib, sys
a = sys.argv
d = pathlib.Path("index") / a[a.index("--release") + 1]
d.mkdir(parents=True, exist_ok=True)
(d / "index.json").write_text(json.dumps({"mode": "preview", "generated_at": "later", "x": 1}))
"""


def index_repo(tmp_path: Path, stub: str) -> Path:
    return git_repo(tmp_path, {"eval/aggregate.py": stub, "index/R/items.csv": "a,b\n",
                               "index/R/index.json": json.dumps({"mode": "preview", "generated_at": "t1", "x": 1})})


def test_an_index_file_aggregate_no_longer_writes_is_reported(tmp_path):
    fails, _ = rp.layer_index(clone_of(index_repo(tmp_path, AGG_STUB)), "R")
    assert [(f.path, f.detail) for f in fails] == [("index/R/items.csv", "檔案被刪除")]


def test_aggregate_exiting_cleanly_without_writing_is_a_failure(tmp_path):
    r = index_repo(tmp_path, "pass\n")
    fails, _ = rp.layer_index(clone_of(r), "R")
    assert [f.path for f in fails] == ["index/R/index.json"] and "沒有寫出" in fails[0].detail
    assert rp.changed_files(r, []) == []


SITE_DATA_STUB = """import json, pathlib, sys
d = pathlib.Path("index") / sys.argv[1]
(d / "site-data").mkdir(parents=True, exist_ok=True)
(d / "site-data" / "manifest.json").write_text(json.dumps({"from": json.loads((d / "index.json").read_text())["generated_at"]}))
"""


def test_site_data_is_rebuilt_from_the_committed_index_and_compared_byte_for_byte(tmp_path):
    """bundle 讀 index.json；要在 index.json 還原成已提交版本之後重建，否則 aggregate 新寫的 generated_at 會混進來。"""
    r = git_repo(tmp_path, {"eval/aggregate.py": AGG_STUB, "tools/site_data.py": SITE_DATA_STUB,
                            "index/R/index.json": json.dumps({"mode": "preview", "generated_at": "t1", "x": 1}),
                            "index/R/site-data/manifest.json": json.dumps({"from": "t1"})})
    fails, _ = rp.layer_index(clone_of(r), "R")
    assert fails == []
    assert rp.changed_files(r, []) == []


def test_a_stale_site_data_bundle_is_reported(tmp_path):
    r = git_repo(tmp_path, {"eval/aggregate.py": AGG_STUB, "tools/site_data.py": SITE_DATA_STUB,
                            "index/R/index.json": json.dumps({"mode": "preview", "generated_at": "t1", "x": 1}),
                            "index/R/site-data/manifest.json": json.dumps({"from": "older"})})
    fails, _ = rp.layer_index(clone_of(r), "R")
    assert [f.path for f in fails] == ["index/R/site-data/manifest.json"]


CHECK_FAILS_STUB = """import sys
print("✗ eval/scores/R/items.jsonl：與由評測紀錄重新產生的內容不同")
sys.exit(1)
"""
INDEX_FILES = {"eval/aggregate.py": AGG_STUB, "index/R/index.json": json.dumps({"mode": "preview", "generated_at": "t1", "x": 1})}


def test_the_score_table_is_checked_when_the_release_has_one(tmp_path):
    """公開版沒有原始紀錄，index 由逐題得分表重算；表本身要通過 eval/score_table.py --check。"""
    r = git_repo(tmp_path, {**INDEX_FILES, "eval/score_table.py": CHECK_FAILS_STUB, "eval/scores/R/runs.json": "{}"})
    fails, done = rp.layer_index(clone_of(r), "R")
    assert [f.path for f in fails] == ["eval/score_table.py --release R --check"]
    assert "與由評測紀錄重新產生的內容不同" in fails[0].detail
    assert "得分表" in done[0] and "沒有原始紀錄" in done[0]


def test_logs_without_a_score_table_fail_the_index_layer(tmp_path):
    """有原始紀錄的 repo 也要提交得分表：少了它，公開版就無法重現 index。"""
    r = git_repo(tmp_path, {**INDEX_FILES, "eval/score_table.py": CHECK_FAILS_STUB, "eval/logs/R/a.eval": "x"})
    fails, done = rp.layer_index(clone_of(r), "R")
    assert [f.path for f in fails] == ["eval/score_table.py --release R --check"]
    assert "與逐題得分表比對" in done[0]


def test_a_passing_score_table_check_adds_no_failure_and_changes_nothing(tmp_path):
    r = git_repo(tmp_path, {**INDEX_FILES, "eval/score_table.py": "pass\n", "eval/scores/R/runs.json": "{}"})
    fails, _ = rp.layer_index(clone_of(r), "R")
    assert fails == []
    assert rp.changed_files(r, []) == []


def test_a_release_with_neither_logs_nor_a_score_table_is_not_checked_for_one(tmp_path):
    r = git_repo(tmp_path, {**INDEX_FILES, "eval/score_table.py": CHECK_FAILS_STUB})
    fails, _ = rp.layer_index(clone_of(r), "R")
    assert fails == []


def test_index_comparison_ignores_generated_at_and_nothing_else():
    a = {"generated_at": "t1", "families": {"x": {"score": 0.5}}, "pins": [1, 2]}
    assert rp.json_first_difference(a, {**a, "generated_at": "t2"}, ignore=("generated_at",)) is None
    assert rp.json_first_difference(a, {**a, "families": {"x": {"score": 0.6}}}, ignore=("generated_at",)) == "families.x.score: 0.5 → 0.6"
    assert rp.json_first_difference(a, {**a, "pins": [1, 3]}, ignore=("generated_at",)) == "pins[1]: 2 → 3"
    assert rp.json_first_difference(a, {**a, "extra": 1}, ignore=("generated_at",)) == "extra: （無） → 1"


# ---- 檢查哪個 release、各層的重跑指令
def _registry(tmp_path):
    (tmp_path / "datasets.yaml").write_text(yaml.safe_dump({"datasets": {
        "laborcalc": {"name": "LaborCalc-TW", "families": ["a"], "releases": ["2026Q4", "laborcalc-2027Q1"],
                      "legacy_releases": ["2026Q4"], "release_name_format": "laborcalc-{period}",
                      "public": {"github": "o/l", "huggingface": "o/L", "templates": "t"}},
        "fakeset": {"name": "FakeSet-TW", "families": ["b"], "releases": ["fakeset-2027Q1"],
                    "release_name_format": "fakeset-{period}",
                    "public": {"github": "o/f", "huggingface": "o/F", "templates": "t2"}}}}, sort_keys=False))


def test_head_checks_each_datasets_latest_release_and_names_the_older_ones(tmp_path):
    """舊 release 的題集由當時的 family 與產生器產生；在 HEAD 重生必然不同，要在它的 release tag 上檢查。
    每個資料集（datasets.yaml）各自的最新 release 都要檢查，不是全部 release 名稱排序後的最後一個。"""
    _registry(tmp_path)
    for d in ("gold/releases/2026Q4/public", "gold/releases/laborcalc-2027Q1/public", "gold/releases/fakeset-2027Q1/public",
              "index/2026Q4"):
        (tmp_path / d).mkdir(parents=True)
    found, fails = rp.dataset_releases(tmp_path)
    assert found == [("laborcalc", "laborcalc-2027Q1", ["2026Q4"]), ("fakeset", "fakeset-2027Q1", [])]
    assert fails == []


def test_a_release_directory_not_in_the_registry_is_a_failure(tmp_path):
    _registry(tmp_path)
    (tmp_path / "gold/releases/scratch/public").mkdir(parents=True)
    _, fails = rp.dataset_releases(tmp_path)
    assert [(f.layer, f.path) for f in fails] == [("datasets", "gold/releases/scratch")]
    _, fails = rp.dataset_releases(tmp_path / "empty")
    assert [f.path for f in fails] == ["datasets.yaml"]


def _latest():
    found, fails = rp.dataset_releases(ROOT)
    assert fails == [] and found
    return [d.latest for d in found if d.latest]


@pytest.mark.parametrize("release", _latest())
def test_gold_is_regenerated_with_each_manifests_release_as_of_and_attempt_count(release):
    cmds, bad = rp.gold_commands(ROOT, release)
    manifests = sorted((ROOT / "gold/releases" / release / "public").glob("*.manifest.json"))
    assert bad == [] and manifests and [k for k, _ in cmds] == [mf.relative_to(ROOT).as_posix() for mf in manifests]
    for (_, argv), mf in zip(cmds, manifests):
        m = json.loads(mf.read_text())
        opt = lambda k: argv[argv.index(k) + 1]  # noqa: E731
        assert argv[:2] == ["gold/generate.py", m["family"]]
        assert (opt("--release"), opt("--split"), opt("--as-of"), opt("--n")) == (m["release"], "public", m["as_of"], str(m["n"] + m["rejected"]))
        assert opt("--pin-generator-version") == m["generator_version"]       # release 釘住自己的產生器版本
        assert argv[-2] == "--as-of"                                          # layer_gold 的報告以最後一個參數當 as_of


def test_private_sets_are_never_regenerated(tmp_path):
    for split in ("public", "private"):
        d = tmp_path / "gold/releases/R" / split
        d.mkdir(parents=True)
        (d / "f.manifest.json").write_text(json.dumps({**MANIFEST, "split": split}))
    cmds, bad = rp.gold_commands(tmp_path, "R")
    assert [k for k, _ in cmds] == ["gold/releases/R/public/f.manifest.json"] and bad == []


@pytest.mark.parametrize("release", _latest())
def test_index_is_recomputed_in_the_mode_it_was_committed_in(release):
    mode = json.loads((ROOT / "index" / release / "index.json").read_text())["mode"]
    assert rp.index_command(ROOT, release) == ["eval/aggregate.py", "--release", release, "--mode", mode]


def test_triage_is_rebuilt_from_zero_then_reviews_are_applied_in_filename_order():
    steps = rp.triage_steps(ROOT, "N0030001")
    assert steps[0] == ["tools/triage.py", "N0030001", "--stage", "merge"]
    files = [s[2] for s in steps[1:]]
    assert all(s[:2] == ["tools/apply_review.py", "N0030001"] for s in steps[1:])
    assert files == sorted(files) and len(files) == len(list((ROOT / "silver/inventory/N0030001/reviews").glob("*.yaml")))


def test_a_crash_inside_a_layer_becomes_that_layers_failure():
    def boom():
        raise KeyError("rejected")
    fails, done = rp.guarded("gold", boom)
    assert [(f.layer, f.path) for f in fails] == [("gold", "（KeyError）")] and "rejected" in fails[0].detail and done == []


# ---- 逐層重跑只在暫存 clone 裡
def test_in_clone_mode_refuses_to_run_outside_a_reproduce_clone(tmp_path, monkeypatch):
    """逐層重跑會改動並還原已追蹤的檔案（git checkout、git clean）；只能在 make reproduce 建立的暫存 clone 裡跑。
    ROOT 指向沒有標記的目錄：這個測試在暫存 clone 裡執行時也不能真的啟動檢查。"""
    monkeypatch.setattr(rp, "ROOT", git_repo(tmp_path, {"a.txt": "a\n"}))
    monkeypatch.delenv(rp.RUNNING_ENV, raising=False)
    with pytest.raises(SystemExit, match="暫存 clone"):
        rp.main(["--in-clone"])
    assert rp.RUNNING_ENV not in os.environ   # 被拒絕時不留下執行中的標記


def test_a_check_cannot_start_another_check_inside_itself(tmp_path, monkeypatch):
    """2026-10-11：檢查跑到 tests 層時，pytest 裡的測試呼叫 main(["--in-clone"])；在有標記的 clone 裡它真的又啟動一次檢查，
    一層套一層停不下來。檢查執行中（環境變數會傳給子程序）不得再啟動檢查。"""
    r = git_repo(tmp_path, {"a.txt": "a\n"})
    (r / ".git" / rp.MARKER).write_text("")
    monkeypatch.setattr(rp, "ROOT", r)
    monkeypatch.setenv(rp.RUNNING_ENV, "1")
    with pytest.raises(SystemExit, match="檢查執行中"):
        rp.main(["--in-clone"])
