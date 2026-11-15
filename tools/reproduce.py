"""從乾淨 clone 重現 bronze → silver → gold → index，逐層比對已提交的檔案。

用法：
  python3.12 tools/reproduce.py              # 或 make reproduce
  python3.12 tools/reproduce.py --online     # 另外從 provenance 記的鏡像 commit 下載法條原檔，核對 sha256
  python3.12 tools/reproduce.py --keep       # 保留暫存目錄（失敗時檢查用）
  python3.12 tools/reproduce.py --rev <tag>  # 檢查別的 commit，例如舊 release 的 tag

做法：把 HEAD clone 到暫存目錄（未提交的改動不在檢查範圍），建新的 venv、只用 requirements.txt 安裝依賴，
再由 clone 裡同一個 commit 的本檔（--in-clone）照 README「跑一遍」重跑每一層，用 git 找出和已提交版本不同的檔案；
每個 commit 用自己的工具檢查自己。每層重跑前先清掉該層的產物（不再產生的檔會顯示為被刪除），比對完就還原，
下一層從已提交的輸入重跑，所以失敗只指向出問題的那一層。任一層不一致：印出層別、檔案與第一個差異，最後回傳 1。

  bronze   依 provenance 的 source_type 分流（沒有這個欄位的舊快照是法規）：
           法規 bronze/<pcode>/source.json 的 sha256 = provenance.json 的 sha256；provenance 的 law_name、law_url、
           last_amended = source.json 的對應欄位（--online：鏡像 commit 上的原檔 sha256 也要相同）
           非法規 bronze/<source_id>/source.<副檔名> 的 sha256 = provenance.json；16 個欄位齊全、source_id = 目錄名
           （tools/fetch_source.py 的 check_snapshot()；沒有鏡像，不做線上核對）
  silver   build_inventory、triage --stage heuristic → inventory.json、prompts/
           刪掉 triage.yaml → triage --stage merge（全部 pending）→ 依檔名順序 apply_review 每個 reviews/*.yaml → triage.yaml
  datasets datasets.yaml 合格式；gold/releases/、index/ 底下的每個 release 都登記在某個資料集；
           index/datasets.json（tools/site_data.py --catalog）逐 byte 相同
  gold     每個資料集最新 release 的 public 目錄：以各 manifest 的 as_of、n + rejected、generator_version（版本標籤釘選）
           重生，整個目錄要等於重生的結果
           （沒有 manifest 的檔也算不一致）；manifest 的 release、split、family 要和所在位置相符；private 不得進 repo
  index    每個資料集最新 release 的 index/<release>/：以 index.json 記的 mode 重跑 aggregate；index.json 只忽略
           generated_at；其餘檔逐 byte 相同。aggregate 的輸入：repo 裡有原始評測紀錄（eval/logs/<release>/*.eval）就用紀錄，
           並與逐題得分表 eval/scores/<release>/ 比對；沒有（公開版不帶原始紀錄）就只讀得分表。
           得分表另以 eval/score_table.py --check 檢查：有紀錄時要與由紀錄重新產生的逐 byte 相同，題數與答對題數要等於 index
  tests    每個 family 的 test_agreement.py；pytest tests/
  其他     重跑完不得有其他檔案被改動或新增

只檢查每個資料集最新的 release（datasets.yaml 的 releases 最後一個）：舊 release 的題集與 index 由當時的 family、
產生器與 aggregate 產生，在 HEAD 重生必然不同，要在它的 release tag 上檢查（--rev <tag>）。

不重建、作為輸入保存的部分：
  llm_labels.json        LLM 第一輪分類的輸出，重跑不會逐字相同；只驗證送給 LLM 的 prompts/ 可以重建
  reviews/*.yaml、families.yaml、silver/families/<id>/reviews.yaml    人工決定
  eval/logs/             模型作答紀錄；重跑模型不會逐字相同，index 由這些 log 重算
  eval/scores/           逐題得分表（由 log 產生，不含作答原文）；repo 裡沒有 log 時，index 由它重算
  private 題集            不進 repo（秘密 salt 只在 TAIWANEVAL_PRIVATE_SALT），不重生
  site/                  頁面內嵌建置時間，不逐 byte 比對

隔離：git 匯出給 hook 的區域變數（GIT_DIR、GIT_INDEX_FILE…）一開始就拿掉，否則對 clone 下的 git 指令會改到真正的 repo；
clone 裡的檢查只拿白名單的環境變數（PYTEST_ADDOPTS 能讓 pytest 只跑一部分而照樣全綠）。Python 以 -E -s -X utf8 執行：
忽略 PYTHON* 環境變數與 user site-packages、不受 locale 影響。不用 -I：-I 會把腳本目錄移出 sys.path，
tools/*.py 彼此 import（paths、family_reviews）會失敗。本檔只用標準庫，依賴都裝在暫存 venv 裡。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_source  # noqa: E402  — bronze 快照的共用判讀（只用標準庫）

ROOT = Path(__file__).resolve().parents[1]
PYTHON_TESTED = (3, 12)
MIRROR_RAW = "https://raw.githubusercontent.com/kong0107/mojLawSplitJSON/{commit}/{path}"
INDEX_IGNORED = ("generated_at",)
MARKER = "taiwaneval-reproduce-clone"   # 寫在暫存 clone 的 .git/ 裡；--in-clone 只在有這個標記的 repo 執行
RUNNING_ENV = "TAIWANEVAL_REPRODUCE_RUNNING"   # 檢查執行中（傳給子程序）；巢狀啟動一律拒絕
PYFLAGS = ("-E", "-s", "-X", "utf8")
CLONE_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
                  "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                  "http_proxy", "https_proxy", "no_proxy", RUNNING_ENV)
PROV_FIELDS = (("law_name", "法規名稱"), ("law_url", "法規網址"), ("last_amended", "最新異動日期"))


class Failure(NamedTuple):
    layer: str
    path: str
    detail: str


# --------------------------------------------------------------------------- 環境
def scrub_git_env() -> None:
    """拿掉 git 匯出給 hook 的區域變數。在 hook 裡執行時它們指向真正的 repo，沿用的話，
    對暫存 clone 下的 checkout 與還原會改到真正的 repo 的 HEAD 與 index。"""
    names = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True,
                           env={"PATH": os.environ.get("PATH", "")}).stdout.split()
    for k in names:
        os.environ.pop(k, None)


def clone_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """clone 裡的檢查只拿白名單的環境變數：PYTEST_ADDOPTS、TAIWANEVAL_PRIVATE_SALT、PIP_*、NODE_OPTIONS、
    git 的區域變數都不該影響重現結果。"""
    base = os.environ if base is None else base
    return {k: base[k] for k in CLONE_ENV_KEEP if k in base}


# --------------------------------------------------------------------------- git
def git(repo: Path, *args: str) -> str:
    # --no-optional-locks：git status 不順手更新 index，對來源 repo 只讀不寫
    return subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def changed_files(repo: Path, pathspecs: list[str]) -> list[tuple[str, str]]:
    """[(狀態, 路徑)]：與 HEAD 不同的檔，含刪除與未追蹤的新檔（.gitignore 的不算）。pathspecs 空 = 整個 repo。
    用 -z：路徑不加引號，中文檔名也照原樣。"""
    entries = git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspecs).split("\0")
    out, i = [], 0
    while i < len(entries):
        e = entries[i]
        if e:
            out.append((e[:2].strip(), e[3:]))
            i += 1 if e[0] not in "RC" else 2   # 改名與複製多一筆原路徑
        else:
            i += 1
    return out


def first_diff(repo: Path, path: str, lines: int = 12, width: int = 160) -> str:
    """第一個 hunk 的前幾行；新檔、刪除的檔直接說明。"""
    status = dict((p, s) for s, p in changed_files(repo, [path])).get(path, "")
    if status == "??":
        return "新檔：已提交的版本沒有這個檔"
    if status == "D":
        return "檔案被刪除"
    out = git(repo, "diff", "--no-color", "--no-ext-diff", "-U1", "--", path).splitlines()
    start = next((i for i, ln in enumerate(out) if ln.startswith("@@")), len(out))
    if start == len(out):
        return "內容不同（二進位檔）"
    hunk = out[start:start + lines]
    # 長行（JSONL 一題一行）的視窗對準第一對 -／+ 行開始不同的地方，否則截掉的部分正好是差異
    minus = next((ln for ln in hunk if ln.startswith("-")), "")
    plus = next((ln for ln in hunk if ln.startswith("+")), "")
    at = next((i for i, (x, y) in enumerate(zip(minus[1:], plus[1:])) if x != y), 0)
    return "\n".join(["（- 已提交　+ 重建）", _clip(hunk[0], 0, width)] + [_clip(ln, at, width) for ln in hunk[1:]])


def _clip(line: str, at: int, width: int) -> str:
    """保留開頭的 diff 記號（-、+、空白），內容取以第 at 字為準的 width 字視窗。"""
    mark, body = line[:1], line[1:]
    if len(body) <= width:
        return line
    lo = max(0, min(at - width // 3, len(body) - width))
    return mark + ("…" if lo else "") + body[lo:lo + width] + ("…" if lo + width < len(body) else "")


def restore(repo: Path, pathspecs: list[str]) -> None:
    """把這些路徑還原成 HEAD 的版本（含被刪的檔），並刪掉未追蹤的新檔。HEAD 沒有的路徑只清新檔。"""
    tracked = [p for p in pathspecs if git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", p).strip()]
    if tracked:
        git(repo, "checkout", "--quiet", "HEAD", "--", *tracked)
    git(repo, "clean", "-fdq", "--", *pathspecs)


def diff_failures(repo: Path, layer: str, pathspecs: list[str]) -> list[Failure]:
    return [Failure(layer, path, first_diff(repo, path)) for _, path in changed_files(repo, pathspecs)]


def clear(d: Path) -> None:
    """清空目錄內容（不刪目錄本身）。重跑前先清：產生器不再寫的檔，比對時會顯示為被刪除。"""
    for p in d.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()


def dirty_note(root: Path, rev: str) -> str | None:
    n = len(changed_files(root, []))
    return f"注意：工作目錄有 {n} 個未提交的改動，不在這次檢查裡；檢查的是已提交的 {rev[:12]}。" if n else None


# --------------------------------------------------------------------------- bronze
def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bronze_dirs(repo: Path) -> list[Path]:
    base = repo / "bronze"
    return sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def check_bronze(repo: Path) -> list[Failure]:
    """依 provenance 的 source_type 分流：法規（沒有 source_type 的舊快照也算）核對 source.json 與法規欄位；
    非法規來源由 tools/fetch_source.py 的 check_snapshot() 核對 16 個欄位、目錄名與 source_file 的 sha256。"""
    fails = []
    for d in bronze_dirs(repo):
        src, prov_p = d / "source.json", d / "provenance.json"
        if prov_p.exists() and fetch_source.source_type(json.loads(prov_p.read_text(encoding="utf-8"))) != "law":
            problems = fetch_source.check_snapshot(d)
            if problems:
                fails.append(Failure("bronze", prov_p.relative_to(repo).as_posix(), "；".join(problems)))
            continue
        if not (src.exists() and prov_p.exists()):
            fails.append(Failure("bronze", d.relative_to(repo).as_posix(), "缺少 source.json 或 provenance.json"))
            continue
        prov, got = json.loads(prov_p.read_text(encoding="utf-8")), sha256_file(src)
        if got != prov["sha256"]:
            fails.append(Failure("bronze", src.relative_to(repo).as_posix(),
                                 f"sha256 {got[:12]}… ≠ provenance.json 記的 {prov['sha256'][:12]}…；bronze 只能由 tools/fetch_law.py 寫入"))
            continue
        law = json.loads(src.read_bytes())
        bad = [f"{k} {prov.get(k)!r} ≠ source.json 的{s} {law.get(s)!r}" for k, s in PROV_FIELDS if prov.get(k) != law.get(s)]
        if bad:
            fails.append(Failure("bronze", prov_p.relative_to(repo).as_posix(), "；".join(bad) + "（這些欄位會進每題的 lineage）"))
    return fails


def mirror_url(prov: dict) -> str | None:
    if "kong0107/mojLawSplitJSON" not in prov.get("source", "") or not prov.get("mirror_commit"):
        return None
    return MIRROR_RAW.format(commit=prov["mirror_commit"], path=prov["source_file"])


def _prov(d: Path) -> dict | None:
    p = d / "provenance.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


_FETCH = ("import certifi, ssl, sys, urllib.request\n"
          "ctx = ssl.create_default_context(cafile=certifi.where())\n"
          "with urllib.request.urlopen(sys.argv[1], timeout=60, context=ctx) as r:\n"
          "    sys.stdout.buffer.write(r.read())\n")


def downloader(python: Path):
    """下載用暫存 venv 的 Python 與其中的 certifi：系統 Python 不一定設好 CA 憑證（python.org 的 macOS 版要另跑
    Install Certificates）。失敗丟 OSError。"""
    def fetch(url: str) -> bytes:
        r = subprocess.run([str(python), *PYFLAGS, "-c", _FETCH, url], capture_output=True)
        if r.returncode:
            last = (r.stderr.decode(errors="replace").strip().splitlines() or ["?"])[-1]
            raise OSError(f"下載 {url} 失敗：{last}")
        return r.stdout
    return fetch


def check_bronze_online(repo: Path, fetch) -> list[Failure]:
    """鏡像 commit 上的原檔 sha256 也要等於 provenance 記的值：證明快照確實來自那個 commit。下載失敗也算不一致。
    沒有 provenance 的快照由 check_bronze 報；沒有鏡像來源的略過（bronze_done 會寫明）。"""
    fails = []
    for d in bronze_dirs(repo):
        prov = _prov(d)
        url = mirror_url(prov) if prov else None
        if url is None:
            continue
        where = (d / "provenance.json").relative_to(repo).as_posix()
        try:
            got = hashlib.sha256(fetch(url)).hexdigest()
        except OSError as e:
            fails.append(Failure("bronze", where, f"無法向鏡像核對：{e}"))
            continue
        if got != prov["sha256"]:
            fails.append(Failure("bronze", where, f"鏡像原檔 {url} 的 sha256 {got[:12]}… ≠ provenance.json 記的 {prov['sha256'][:12]}…"))
    return fails


def bronze_done(repo: Path, online: bool) -> list[str]:
    out = []
    for d in bronze_dirs(repo):
        prov = _prov(d)
        st = fetch_source.source_type(prov) if prov else "law"
        if st != "law":
            out.append(f"{d.relative_to(repo).as_posix()}/{prov.get('source_file')} sha256 = provenance.json；"
                       f"{st}，provenance 欄位齊全" + ("（非法規來源，未做線上核對）" if online else ""))
            continue
        line = f"{d.relative_to(repo).as_posix()}/source.json sha256 = provenance.json；law_name、law_url、last_amended 相符"
        if online:
            prov = _prov(d)
            line += "；= 鏡像 commit 上的原檔" if prov and mirror_url(prov) else "（provenance 沒有可下載的來源，未做線上核對）"
        out.append(line)
    return out


# --------------------------------------------------------------------------- 檢查哪個 release、各層的重跑指令（相對 repo 根目錄）
class DatasetReleases(NamedTuple):
    dataset: str
    latest: str | None
    older: list[str]


def dataset_releases(repo: Path) -> tuple[list[DatasetReleases], list[Failure]]:
    """每個資料集（datasets.yaml）的 (最新 release, 較舊的 release)，以及登錄檔本身或沒登記的 release 目錄造成的不一致。

    HEAD 只檢查每個資料集最新的 release；舊的要在它的 release tag 上檢查。"""
    import dataset_registry   # 讀 YAML：只在暫存 venv 裡（--in-clone）呼叫，本檔其他部分仍只用標準庫
    try:
        reg = dataset_registry.load(repo / "datasets.yaml")
    except dataset_registry.RegistryError as e:
        return [], [Failure("datasets", "datasets.yaml", str(e))]
    out = [DatasetReleases(d.id, d.latest_release, list(d.releases[:-1])) for d in reg.datasets]
    fails = []
    for base in ("gold/releases", "index"):
        if (repo / base).is_dir():
            for p in sorted(x for x in (repo / base).iterdir() if x.is_dir()):
                if reg.dataset_of_release(p.name) is None:
                    fails.append(Failure("datasets", f"{base}/{p.name}", "這個 release 沒有登記在 datasets.yaml 的任何資料集"))
    return out, fails


def layer_datasets(c: "Clone") -> tuple[list[Failure], list[str]]:
    """登錄檔、release 目錄的歸屬，與 index/datasets.json 重建比對。"""
    found, fails = dataset_releases(c.repo)
    if not fails and (c.repo / "index/datasets.json").exists():
        f = c.run("datasets", ["tools/site_data.py", "--catalog"])
        fails += [f] if f else diff_failures(c.repo, "datasets", ["index/datasets.json"])
        restore(c.repo, ["index/datasets.json"])
    elif not fails:
        fails.append(Failure("datasets", "index/datasets.json", "沒有提交（python3 tools/site_data.py --catalog）"))
    done = [f"{d.dataset}：最新 release {d.latest}" + (f"（較舊：{'、'.join(d.older)}）" if d.older else "") for d in found]
    return fails, done + ["index/datasets.json 從 datasets.yaml 重建、逐 byte"]


def inventory_steps(pcode: str) -> list[list[str]]:
    return [["tools/build_inventory.py", pcode], ["tools/triage.py", pcode, "--stage", "heuristic"]]


def triage_steps(repo: Path, pcode: str) -> list[list[str]]:
    """從零 merge（不能有舊的 triage.yaml，否則會沿用其中的人工決定），再依檔名（日期開頭）順序套用每個 reviews 檔。"""
    reviews = sorted((repo / "silver/inventory" / pcode / "reviews").glob("*.yaml"))
    return [["tools/triage.py", pcode, "--stage", "merge"],
            *(["tools/apply_review.py", pcode, rv.relative_to(repo).as_posix()] for rv in reviews)]


def gold_commands(repo: Path, release: str) -> tuple[list[tuple[str, list[str]]], list[Failure]]:
    """這個 release 的 public 題集：[(manifest 路徑, generate 指令)]，以及內容與位置對不上、或缺欄位的 manifest。
    只重生 public；private 不進 repo，也沒有 salt。"""
    cmds, bad = [], []
    for mf in sorted((repo / "gold/releases" / release / "public").glob("*.manifest.json")):
        rel = mf.relative_to(repo).as_posix()
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            want = {"release": release, "split": "public", "family": mf.name.removesuffix(".manifest.json")}
            wrong = [f"{k} 記的是 {m.get(k)!r}，所在位置是 {v!r}" for k, v in want.items() if m.get(k) != v]
            if wrong:
                bad.append(Failure("gold", rel, "manifest 與所在位置不符：" + "；".join(wrong)))
                continue
            if not m.get("as_of"):
                bad.append(Failure("gold", rel, "manifest 沒有 as_of，無法以原日期重生"))
                continue
            # release 釘住自己的產生器版本：以 manifest 記的版本標籤重生，其餘內容要逐 byte 相同
            pin = ["--pin-generator-version", m["generator_version"]] if m.get("generator_version") else []
            cmds.append((rel, ["gold/generate.py", m["family"], "--release", release, "--split", "public",
                               "--n", str(m["n"] + m.get("rejected", 0)), *pin, "--as-of", m["as_of"]]))
        except (ValueError, KeyError, TypeError) as e:
            bad.append(Failure("gold", rel, f"manifest 讀不出重生需要的欄位：{e!r}"))
    return cmds, bad


def tracked_private(repo: Path) -> list[Failure]:
    out = git(repo, "ls-files", "-z", "--", ":(glob)gold/releases/*/private/**")
    return [Failure("gold", p, "private 題集不得進 repo：git rm --cached 移除，並確認是否已外流")
            for p in out.split("\0") if p]


def index_command(repo: Path, release: str) -> list[str] | None:
    """aggregate 指令，mode 用已提交的 index.json 記的那個；這個 release 沒有 index 時為 None。"""
    ij = repo / "index" / release / "index.json"
    if not ij.exists():
        return None
    return ["eval/aggregate.py", "--release", release, "--mode", json.loads(ij.read_text(encoding="utf-8")).get("mode", "preview")]


# --------------------------------------------------------------------------- 比對 index.json
def _show(v) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 80 else s[:80] + "…"


def json_first_difference(a, b, ignore: tuple[str, ...] = (), path: str = "") -> str | None:
    """第一個不同之處，例如 "families.x.score: 0.5 → 0.6"；ignore 只作用在最上層的欄位。"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if not path and k in ignore:
                continue
            p = f"{path}.{k}" if path else k
            if k not in a or k not in b:
                return f"{p}: {'（無）' if k not in a else _show(a[k])} → {'（無）' if k not in b else _show(b[k])}"
            d = json_first_difference(a[k], b[k], path=p)
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = json_first_difference(x, y, path=f"{path}[{i}]")
            if d:
                return d
        return None if len(a) == len(b) else f"{path}: 長度 {len(a)} → {len(b)}"
    return None if a == b and type(a) is type(b) else f"{path}: {_show(a)} → {_show(b)}"


# --------------------------------------------------------------------------- 在 clone 裡逐層重跑
class Clone:
    def __init__(self, repo: Path, python: Path):
        self.repo, self.python = repo, python

    def run(self, layer: str, argv: list[str]) -> Failure | None:
        r = subprocess.run([str(self.python), *PYFLAGS, *argv], cwd=self.repo, capture_output=True,
                           encoding="utf-8", errors="replace", env=clone_env())
        if r.returncode == 0:
            return None
        tail = "\n".join((r.stderr.strip() or r.stdout.strip()).splitlines()[-8:])
        return Failure(layer, " ".join(argv), f"結束碼 {r.returncode}\n{tail}")

    def run_all(self, layer: str, steps: list[list[str]]) -> list[Failure]:
        for argv in steps:
            f = self.run(layer, argv)
            if f:
                return [f]
        return []


def layer_silver(c: Clone) -> tuple[list[Failure], list[str]]:
    fails, done = [], []
    inv_root = c.repo / "silver/inventory"
    for inv in sorted(p for p in inv_root.iterdir() if p.is_dir()) if inv_root.is_dir() else []:
        pcode, base = inv.name, inv.relative_to(c.repo).as_posix()
        if (inv / "inventory.json").exists():
            scope = [f"{base}/inventory.json", f"{base}/prompts"]
            (inv / "inventory.json").unlink()                  # 先清掉產物：不再產生的 prompts 批次會顯示為被刪除
            shutil.rmtree(inv / "prompts", ignore_errors=True)
            fails += c.run_all("silver", inventory_steps(pcode)) or diff_failures(c.repo, "silver", scope)
            restore(c.repo, scope)
            done.append(f"{pcode} inventory.json、prompts/")
        if (inv / "triage.yaml").exists():
            scope = [f"{base}/triage.yaml"]
            (inv / "triage.yaml").unlink()
            steps = triage_steps(c.repo, pcode)
            fails += c.run_all("silver", steps) or diff_failures(c.repo, "silver", scope)
            restore(c.repo, scope)
            done.append(f"{pcode} triage.yaml（從零 merge，套用 {len(steps) - 1} 個 reviews 檔）")
    return fails, done


def layer_gold(c: Clone, release: str) -> tuple[list[Failure], list[str]]:
    """重生到 repo 外的暫存目錄，清空 public 目錄後蓋回，用 git 比對：被改過的檔指出差在哪，沒有 manifest 的檔、
    產生器不再寫的檔顯示為被刪除。不在原處重生：generate 的版本守門會擋下「版本沒變、題目卻不同」並要求 bump 版本，
    那在這裡會誤導。"""
    fails = tracked_private(c.repo)
    pub = c.repo / "gold/releases" / release / "public"
    if not pub.is_dir():
        return fails, []
    cmds, bad = gold_commands(c.repo, release)
    fails += bad
    scope = [pub.relative_to(c.repo).as_posix()]
    with tempfile.TemporaryDirectory(prefix="regen-") as out:
        ran = [c.run("gold", [*argv, "--out-dir", out]) for _, argv in cmds]
        fails += [f for f in ran if f]
        if not bad and not any(ran):
            clear(pub)
            for p in sorted(Path(out).iterdir()):
                shutil.copyfile(p, pub / p.name)
            fails += diff_failures(c.repo, "gold", scope)
    restore(c.repo, scope)
    return fails, [f"{release}/public {argv[1]}（as_of {argv[-1]}" + (f"，generator {argv[argv.index('--pin-generator-version') + 1]}"
                                                                     if "--pin-generator-version" in argv else "") + "）" for _, argv in cmds]


def score_inputs(repo: Path, release: str) -> tuple[bool, bool]:
    """(有沒有原始評測紀錄, 有沒有逐題得分表)。aggregate 有紀錄就由紀錄算，沒有就讀得分表（公開版）。"""
    return (any((repo / "eval/logs" / release).glob("*.eval")),
            (repo / "eval/scores" / release / "runs.json").exists())


def layer_index(c: Clone, release: str) -> tuple[list[Failure], list[str]]:
    """重跑 aggregate 與 site_data 並比對；再檢查逐題得分表（eval/score_table.py --check）：有原始紀錄時，表要與由紀錄
    重新產生的逐 byte 相同（沒有表也算不一致）；表裡每個 run 的題數、答對題數要等於已提交的 index。"""
    argv = index_command(c.repo, release)
    if argv is None:
        return [], []
    rel_dir, ij = f"index/{release}", f"index/{release}/index.json"
    committed = json.loads((c.repo / ij).read_text(encoding="utf-8"))
    fails = []
    clear(c.repo / rel_dir)                                     # 先清掉產物：aggregate 不再寫的檔會顯示為被刪除
    f = c.run("index", argv)
    if f:
        fails.append(f)
    elif not (c.repo / ij).exists():
        fails.append(Failure("index", ij, "aggregate 結束碼 0，卻沒有寫出 index.json"))
    else:
        d = json_first_difference(committed, json.loads((c.repo / ij).read_text(encoding="utf-8")), ignore=INDEX_IGNORED)
        if d:
            fails.append(Failure("index", ij, f"忽略 {'、'.join(INDEX_IGNORED)} 後仍不同：{d}"))
        restore(c.repo, [ij])
        bundle = (c.repo / "tools/site_data.py").exists()       # site-data bundle 讀已提交的 index.json，所以在還原之後重建
        f = c.run("index", ["tools/site_data.py", release]) if bundle else None
        if f:
            fails.append(f)
        fails += diff_failures(c.repo, "index", [rel_dir])
    restore(c.repo, [rel_dir])
    has_logs, has_table = score_inputs(c.repo, release)
    checked = (c.repo / "eval/score_table.py").exists() and (has_logs or has_table)
    if checked:                                                 # 讀已提交的 index.json，所以在還原之後；不寫任何檔
        f = c.run("index", ["eval/score_table.py", "--release", release, "--check"])
        if f:
            fails.append(f)
    source = ("由 eval/logs 的原始紀錄重算" + ("，並與逐題得分表比對" if checked else "") if has_logs
              else "沒有原始紀錄，由逐題得分表 eval/scores 重算" if has_table else "沒有評測紀錄")
    return fails, [f"{release}（{argv[-1]}）{source}；index.json 忽略 generated_at、其餘檔逐 byte"
                   + ("；site-data 從已提交的 index.json 重建、逐 byte" if (c.repo / "tools/site_data.py").exists() else "")]


def layer_tests(c: Clone) -> tuple[list[Failure], list[str]]:
    fails, done = [], []
    for t in sorted((c.repo / "silver/families").glob("*/test_agreement.py")):
        rel = t.relative_to(c.repo).as_posix()
        fails += c.run_all("tests", [[rel]])
        done.append(rel)
    fails += c.run_all("tests", [["-m", "pytest", "-q", "--tb=no", "-rfE", "-p", "no:cacheprovider", "tests/"]])
    done.append("pytest tests/")
    return fails, done


def guarded(layer: str, fn, *args) -> tuple[list[Failure], list[str]]:
    """執行一層；工具或比對本身丟出例外時，記成這一層的不一致，其他層照跑、照報。"""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 — 任何例外都要變成報告裡的一行
        return [Failure(layer, f"（{type(e).__name__}）", "".join(traceback.format_exception_only(e)).strip())], []


def _crashed(fails: list[Failure]) -> bool:
    return any(f.path.startswith("（") and f.path.endswith("）") for f in fails)


# --------------------------------------------------------------------------- 準備 clone 與環境
def step(msg: str, argv: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"{msg}失敗（結束碼 {r.returncode}）：\n{r.stderr.strip() or r.stdout.strip()}")


def resolve_rev(repo: Path, rev: str) -> str:
    """把 rev（commit、tag、分支）解析成完整 SHA。clone 裡只有 origin/<分支>，直接 checkout 分支名會失敗。"""
    for cand in (rev, f"origin/{rev}"):
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    raise SystemExit(f"找不到 {rev}：不是這個 repo 裡的 commit、tag 或分支")


def install_commands(vpy: Path, req: Path) -> tuple[list[str], list[str]]:
    """只裝 requirements.txt 列的版本（--no-deps：清單漏列的依賴不會被悄悄裝上未鎖定的版本），再以 pip check 確認清單完整。
    pip 的來源設定（PIP_INDEX_URL、pip.conf）照常生效：它只決定從哪裡下載，版本由清單鎖定。"""
    base = [str(vpy), *PYFLAGS, "-m", "pip"]
    return [*base, "install", "--quiet", "--disable-pip-version-check", "--no-deps", "-r", str(req)], [*base, "check"]


def prepare(work: Path, source: str, rev: str | None, python: str) -> Clone:
    repo, venv = work / "repo", work / "venv"
    step("clone ", ["git", "clone", "--quiet", "--no-hardlinks", source, str(repo)])
    if rev:
        step("checkout ", ["git", "-C", str(repo), "checkout", "--quiet", "--detach", resolve_rev(repo, rev)])
    step("建立 venv ", [python, "-m", "venv", str(venv)])
    vpy = venv / "bin" / "python"
    install, check = install_commands(vpy, repo / "requirements.txt")
    step("安裝 requirements.txt ", install)
    step("pip check（requirements.txt 漏列了依賴？）", check)
    return Clone(repo, vpy)


def report(layer: str, fails: list[Failure], done: list[str]) -> None:
    print(f"== {layer}")
    for d in done:
        print(f"  ✓ {d}" if not fails else f"  · {d}")
    for f in fails:
        print(f"  ✗ {f.path}")
        for ln in f.detail.splitlines():
            print(f"      {ln}")


def check_here(online: bool) -> int:
    """在暫存 clone 裡逐層重跑與比對（main 以 --in-clone 呼叫，用的是這個 commit 自己的 tools）。"""
    if os.environ.get(RUNNING_ENV):
        raise SystemExit("已經有一次 reproduce 檢查執行中（tests 層的 pytest 裡又呼叫了檢查？）；不啟動巢狀檢查。")
    if not (ROOT / ".git" / MARKER).exists():
        raise SystemExit("--in-clone 只能在 tools/reproduce.py 建立的暫存 clone 裡執行：它會改動並還原已追蹤的檔案。"
                         "請用 make reproduce。")
    os.environ[RUNNING_ENV] = "1"   # 傳給之後的每個子程序
    c = Clone(ROOT, Path(sys.executable))
    head = git(c.repo, "rev-parse", "HEAD").strip()
    pyv = ".".join(map(str, sys.version_info[:3]))
    node = shutil.which("node")
    nodev = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else "找不到 node"
    print(f"commit {head[:12]}｜Python {pyv}｜Node {nodev}")
    if sys.version_info[:2] != PYTHON_TESTED:
        print(f"注意：requirements.txt 以 Python {'.'.join(map(str, PYTHON_TESTED))} 驗證，這次用的是 {pyv}")

    fails: list[Failure] = []

    def layer(name: str, fn, *args) -> None:
        lf, done = guarded(name, fn, *args)
        report(name, lf, done)
        if _crashed(lf):            # 這層中途出錯：還原整個 clone，下一層從已提交的狀態開始
            restore(c.repo, ["."])
        fails.extend(lf)

    layer("bronze", lambda: (check_bronze(c.repo) + (check_bronze_online(c.repo, downloader(c.python)) if online else []),
                             bronze_done(c.repo, online)))
    layer("silver", layer_silver, c)
    layer("datasets", layer_datasets, c)
    found, _ = dataset_releases(c.repo)
    for d in found:                                   # 每個資料集的最新 release 各自重生、各自比對
        if d.latest:
            layer(f"gold {d.dataset}", layer_gold, c, d.latest)
            layer(f"index {d.dataset}", layer_index, c, d.latest)
    older = [r for d in found for r in d.older]
    if older:
        print(f"  （較舊的 release {'、'.join(older)} 由當時的 family、產生器與 aggregate 產生，不在 HEAD 重跑；"
              "請在各自的 release tag 上檢查：tools/reproduce.py --rev <tag>）")
    layer("tests", layer_tests, c)
    other = [Failure("其他", p, "重跑後被改動，不在任何一層的比對範圍") for _, p in changed_files(c.repo, [])]
    report("其他", other, ["沒有其他檔案被改動或新增"] if not other else [])
    fails += other

    if fails:
        layers: dict[str, int] = {}
        for f in fails:
            layers[f.layer] = layers.get(f.layer, 0) + 1
        print(f"\n✗ {len(fails)} 處不一致（{'、'.join(f'{k} {v}' for k, v in layers.items())}）。")
        return 1
    print(f"\n✓ 全部一致：{head[:12]} 可以從乾淨 clone 重現。")
    return 0


def main(argv: list[str] | None = None) -> int:
    scrub_git_env()
    ap = argparse.ArgumentParser(description="從乾淨 clone 重現 bronze → silver → gold → index")
    ap.add_argument("--repo", default=str(ROOT), help="要 clone 的 repo（路徑或網址），預設這個 repo")
    ap.add_argument("--rev", default=None, help="要檢查的 commit、tag 或分支；預設 --repo 為本 repo 時用它的 HEAD")
    ap.add_argument("--python", default=sys.executable, help="建立 venv 用的 Python（預設執行本檔的這個）")
    ap.add_argument("--online", action="store_true", help="另外從鏡像 commit 下載法條原檔核對 sha256")
    ap.add_argument("--keep", action="store_true", help="保留暫存目錄")
    ap.add_argument("--in-clone", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.in_clone:
        return check_here(a.online)

    rev, note = a.rev, None
    if Path(a.repo).resolve() == ROOT:
        rev = rev or git(ROOT, "rev-parse", "HEAD").strip()
        note = dirty_note(ROOT, rev)
        if note:
            print(note, flush=True)
    work = Path(tempfile.mkdtemp(prefix="taiwaneval-reproduce-"))
    try:
        c = prepare(work, a.repo, rev, a.python)
        (c.repo / ".git" / MARKER).write_text("tools/reproduce.py 建立的暫存 clone\n")
        print(f"暫存目錄 {work}", flush=True)
        # 逐層檢查交給 clone 裡同一個 commit 的 tools/reproduce.py：每個 commit 用自己的工具檢查自己
        code = subprocess.run([str(c.python), *PYFLAGS, "tools/reproduce.py", "--in-clone",
                               *(["--online"] if a.online else [])], cwd=c.repo, env=clone_env()).returncode
    finally:
        if a.keep:
            print(f"保留暫存目錄：{work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    if note:
        print(note)
    if code and not a.keep:
        print("加 --keep 保留暫存目錄檢查。")
    return code


if __name__ == "__main__":
    sys.exit(main())
