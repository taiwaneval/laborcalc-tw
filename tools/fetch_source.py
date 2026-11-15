"""把一份非法規來源寫進 bronze（docs/bronze-non-law-sources.md 第 2–4 節）。

法規用 tools/fetch_law.py（bronze/<pcode>/source.json）。本工具收另外兩種來源類型：
  announcement  機關公告、函釋、行政規則、說明文件
  dataset       政府開放資料或機關發布的對照表、代碼表
calculator（官方試算系統的前端程式）不進 bronze，放 docs/verification/（docs/family-admission.md B4）。

輸出 bronze/<source_id>/source.<副檔名>（原樣 bytes）與 provenance.json（16 個欄位，見 PROVENANCE_FIELDS）。
sha256 由本工具計算。目錄已存在時拒絕寫入，沒有覆蓋選項：同一來源的新版本另開 bronze/<source_id>-<版本或日期>/。

兩種取得方式，都記 retrieved_url（實際抓取 bytes 的網址）：
  python3 tools/fetch_source.py mof-ban-checksum --type announcement --title … --publisher … \\
      --url <官方頁面> [--retrieved-url <附件網址>] --published-on 2024-05-01 --note …
  python3 tools/fetch_source.py mof-ban-checksum … --file 已下載.pdf --retrieved-url <下載它的網址>
官方網站擋自動抓取時用 --file。

例外：沒有版次的官方網頁表格（docs/bronze-non-law-sources.md 第 3 節）。dataset 類的 HTML 表格，頁面與原檔
都沒有公告日、字號或版次時，加 --unversioned-web-table 才能讓三個版本欄位都是 null。本工具在 note 補記伺服器回應的
Last-Modified（本機檔模式用 --last-modified 照抄）與「排除每次請求都會變的內容之後」的內容 sha256（content_sha256()）；
保存的檔與 sha256 欄位仍是原始 bytes。日後重新抓取用 --recheck 比對內容 hash，不寫任何檔：
  python3 tools/fetch_source.py moi-ris-year-table --recheck

本模組也是 bronze 快照的共用判讀（tools/reproduce.py、tools/site_data.py、gold/generate.py、tests/test_fetch_law.py）：
source_type() 把沒有 source_type 的舊快照視為 law；snapshot_file() 找出快照檔；check_snapshot() 核對一個非法規快照。
只用標準庫：tools/reproduce.py 在建 venv 之前就會 import 本檔。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import bronze_dir  # noqa: E402

TOOL_VERSION = "0.2.0"     # 0.2.0：沒有版次的官方網頁表格例外、content_sha256()、--recheck
SOURCE_TYPES = ("law", "announcement", "dataset", "calculator")
WRITABLE_TYPES = ("announcement", "dataset")
PROVENANCE_FIELDS = ("source_type", "source_id", "title", "publisher", "url", "retrieved_url", "published_on",
                     "document_number", "version", "source_file", "media_type", "sha256", "fetched_at", "fetched_by",
                     "license", "note")
VERSION_IDS = ("published_on", "document_number", "version")      # 至少一個不是 null，否則無法判斷來源的版本
SOURCE_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")             # 法規 pcode 是大寫字母開頭，兩者不會衝突
EXTENSIONS = {"application/pdf": "pdf", "text/html": "html", "application/xhtml+xml": "html", "application/json": "json",
              "text/csv": "csv", "application/xml": "xml", "text/xml": "xml", "text/plain": "txt",
              "application/zip": "zip", "application/vnd.oasis.opendocument.text": "odt",
              "application/vnd.oasis.opendocument.spreadsheet": "ods",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}
TZ = timezone(timedelta(hours=8))

# 每次請求都會變、計算內容 hash 時要排除的片段：(代號, 說明, pattern)。只排除這個元素本身，同一行的其他內容保留
# （〈年歲對照表〉的 Incapsula script 與 </body> 在同一行）。
VOLATILE = [
    ("incapsula-script", "Incapsula 防火牆插入的 <script>（src 以 /_Incapsula_Resource? 開頭）",
     re.compile(rb'<script\b[^>]*\bsrc="/_Incapsula_Resource\?[^"]*"[^>]*>\s*</script>')),
]
CONTENT_HASH = re.compile(r"排除後的內容 sha256: ([0-9a-f]{64})")
UNVERSIONED_TYPES = ("dataset",)
UNVERSIONED_MEDIA = ("text/html", "application/xhtml+xml")


# --------------------------------------------------------------------------- 共用判讀
def stable_content(raw: bytes) -> tuple[bytes, list[tuple[str, int]]]:
    """去掉 VOLATILE 列的片段：(去掉後的 bytes, [(代號, 處數)])。只用來算內容 hash，bronze 保存的仍是原始 bytes。"""
    removed = []
    for key, _, pat in VOLATILE:
        raw, n = pat.subn(b"", raw)
        if n:
            removed.append((key, n))
    return raw, removed


def content_sha256(raw: bytes) -> str:
    """排除每次請求都會變的內容之後的 sha256；判斷重新抓取的內容是否改變時用它，不用原始 bytes 的 hash。"""
    return hashlib.sha256(stable_content(raw)[0]).hexdigest()


def unversioned_note(raw: bytes, last_modified: str | None) -> str:
    """例外要求 note 補記的兩件事（docs/bronze-non-law-sources.md 第 3 節例外的條件 1、2）。"""
    lm = (f"Last-Modified: {last_modified}（伺服器中繼資料，不是官方日期）" if last_modified
          else "無 Last-Modified（伺服器沒有回這個標頭，或本機檔模式沒有提供）")
    _, removed = stable_content(raw)
    labels = {k: label for k, label, _ in VOLATILE}
    if removed:
        what = "；".join(f"{labels[k]}，{n} 處" for k, n in removed)
        vol = f"排除每次請求都會變的內容：{what}；sha256 欄位仍是原始 bytes 的 hash"
    else:
        vol = "沒有每次請求都會變的內容"
    return (f"沒有版次的官方網頁表格（docs/bronze-non-law-sources.md 第 3 節例外）。{lm}。{vol}；"
            f"排除後的內容 sha256: {content_sha256(raw)}。")


def _unversioned_problems(prov: dict, f: Path) -> list[str]:
    """三個版本欄位都是 null 時，只有符合例外的條件才通過。"""
    if prov.get("source_type") not in UNVERSIONED_TYPES or prov.get("media_type") not in UNVERSIONED_MEDIA:
        return [f"{'、'.join(VERSION_IDS)} 都是 null：無法判斷來源的版本"
                "（例外只適用於 dataset 類的 HTML 表格，docs/bronze-non-law-sources.md 第 3 節）"]
    note = prov.get("note") or ""
    problems = []
    if "Last-Modified" not in note:
        problems.append("三個版本欄位都是 null，但 note 沒有記 Last-Modified（或「無 Last-Modified」）")
    m = CONTENT_HASH.search(note)
    if not m:
        problems.append("三個版本欄位都是 null，但 note 沒有記「排除後的內容 sha256」")
    elif f.exists() and m.group(1) != content_sha256(f.read_bytes()):
        problems.append(f"note 記的排除後的內容 sha256 {m.group(1)[:12]}… ≠ 重算的 {content_sha256(f.read_bytes())[:12]}…")
    return problems


def source_type(prov: dict) -> str:
    """沒有 source_type 的舊快照（tools/fetch_law.py 寫的）是法規。"""
    return prov.get("source_type", "law")


def snapshot_file(d: Path, prov: dict) -> Path:
    """快照檔的位置。法規的 provenance 裡 source_file 是鏡像上的路徑（FalVMingLing/<pcode>.json），
    repo 裡的檔一律叫 source.json；非法規來源的 source_file 就是目錄裡的檔名。"""
    return d / "source.json" if source_type(prov) == "law" else d / prov["source_file"]


def check_snapshot(d: Path) -> list[str]:
    """核對一個非法規快照；回傳問題（空 list = 沒問題）。法規的欄位核對在 tools/reproduce.py 的 check_bronze()。"""
    p = d / "provenance.json"
    if not p.exists():
        return ["缺少 provenance.json"]
    prov = json.loads(p.read_text(encoding="utf-8"))
    st = source_type(prov)
    problems = []
    if st == "law":
        f = snapshot_file(d, prov)
    else:
        missing = [k for k in PROVENANCE_FIELDS if k not in prov]
        if missing:
            problems.append(f"provenance.json 缺少欄位：{', '.join(missing)}（沒有值寫 null，不能省略）")
        if st not in WRITABLE_TYPES:
            problems.append(f"source_type {st!r} 不能進 bronze（只收 {', '.join(WRITABLE_TYPES)}；法規用 tools/fetch_law.py）")
        if prov.get("source_id") != d.name:
            problems.append(f"source_id {prov.get('source_id')!r} ≠ 目錄名 {d.name!r}")
        name = prov.get("source_file")
        if all(prov.get(k) is None for k in VERSION_IDS):
            problems += _unversioned_problems(prov, d / name if isinstance(name, str) and "/" not in name else d / "-")
        if not isinstance(name, str) or "/" in name or not name.startswith("source."):
            problems.append(f"source_file {name!r} 應是目錄裡的 source.<副檔名>")
            return problems
        f = d / name
        extra = sorted(x.name for x in d.iterdir() if x.name not in ("provenance.json", name))
        if extra:
            problems.append(f"目錄裡有 provenance 沒記的檔：{', '.join(extra)}")
    if not f.exists():
        return problems + [f"缺少 {f.name}"]
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    if got != prov.get("sha256"):
        problems.append(f"{f.name} 的 sha256 {got[:12]}… ≠ provenance.json 記的 {str(prov.get('sha256'))[:12]}…；"
                        "bronze 只能由 tools/fetch_law.py、tools/fetch_source.py 寫入")
    return problems


# --------------------------------------------------------------------------- 取得 bytes
def download(url: str) -> tuple[bytes, str | None]:
    """(bytes, Content-Type)。有 certifi 就用它的 CA（系統 Python 不一定設好憑證）。"""
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": f"taiwaneval-fetch-source/{TOOL_VERSION}"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return r.read(), r.headers.get("Content-Type")


def download_meta(url: str) -> tuple[bytes, str | None, str | None]:
    """(bytes, Content-Type, Last-Modified)。例外與 --recheck 要記 Last-Modified。"""
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": f"taiwaneval-fetch-source/{TOOL_VERSION}"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return r.read(), r.headers.get("Content-Type"), r.headers.get("Last-Modified")


def _media(content_type: str | None) -> str | None:
    return content_type.split(";")[0].strip().lower() if content_type else None


def _dump(prov: dict) -> str:
    return json.dumps(prov, ensure_ascii=False, indent=1) + "\n"


# --------------------------------------------------------------------------- 寫入
def fail(msg: str):
    raise SystemExit(f"fetch_source：{msg}。未寫入任何檔案。")


def validate(a) -> None:
    if not SOURCE_ID.match(a.source_id):
        fail(f"source_id {a.source_id!r} 要用小寫英數與單一連字號，開頭是發布機關的簡稱（例如 mof-ban-checksum）")
    if a.type == "law":
        fail("法規用 tools/fetch_law.py 收進 bronze/<pcode>/")
    if a.type == "calculator":
        fail("calculator 類不進 bronze，放 docs/verification/<日期>-<來源>/（docs/family-admission.md B4）")
    if a.published_on is not None:
        try:
            date.fromisoformat(a.published_on)
        except ValueError:
            fail(f"--published-on {a.published_on!r} 要是 ISO 日期（YYYY-MM-DD），照官方文件換算後填")
    if a.unversioned_web_table and a.type not in UNVERSIONED_TYPES:
        fail("--unversioned-web-table 只適用於 dataset 類的官方網頁表格（docs/bronze-non-law-sources.md 第 3 節例外）")
    if all(getattr(a, k) is None for k in VERSION_IDS) and not a.unversioned_web_table:
        fail("published_on、document_number、version 至少要有一個，否則無法判斷來源的版本"
             "（沒有版次的官方網頁表格見 docs/bronze-non-law-sources.md 第 3 節例外與 --unversioned-web-table）")
    if a.note is None and any(getattr(a, k) is None for k in VERSION_IDS):
        fail("有欄位是 null 時要用 --note 說明原因（docs/bronze-non-law-sources.md 第 3 節）")
    if a.file is not None and a.retrieved_url is None:
        fail("--file 模式要用 --retrieved-url 記下這個檔是從哪個網址下載的")
    if a.last_modified is not None and a.file is None:
        fail("--last-modified 只在 --file 模式使用；從網路抓時由回應的標頭取得")


def recheck(source_id: str, retrieved_url: str | None = None) -> bool:
    """重新抓取 bronze/<source_id>/ 的來源，以排除後的內容 hash 判斷是否改變（例外的條件 3）。不寫任何檔。
    True = 內容未變。內容改變時依第 2 節另開 bronze/<source_id>-<版本或日期>/，不覆蓋舊目錄。"""
    d = bronze_dir(source_id)
    prov = json.loads((d / "provenance.json").read_text(encoding="utf-8"))
    m = CONTENT_HASH.search(prov.get("note") or "")
    old = m.group(1) if m else content_sha256((d / prov["source_file"]).read_bytes())
    raw, _, lm = download_meta(retrieved_url or prov["retrieved_url"])
    new = content_sha256(raw)
    if new == old:
        print(f"{source_id}：內容未變（排除後的內容 sha256 {new}；Last-Modified {lm or '無'}）")
        return True
    print(f"{source_id}：內容已變（排除後的內容 sha256 {old[:12]}… → {new[:12]}…；Last-Modified {lm or '無'}）。"
          f"依 docs/bronze-non-law-sources.md 第 2 節另開 bronze/{source_id}-<版本或日期>/，不覆蓋舊目錄")
    return False


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="把一份非法規來源寫進 bronze/<source_id>/")
    ap.add_argument("source_id")
    ap.add_argument("--type", required=True, choices=SOURCE_TYPES)
    ap.add_argument("--title", required=True, help="文件的正式名稱")
    ap.add_argument("--publisher", required=True, help="發布機關的全名")
    ap.add_argument("--url", required=True, help="官方頁面的正式網址")
    ap.add_argument("--retrieved-url", default=None, help="實際抓取 bytes 的網址；省略時抓 --url")
    ap.add_argument("--file", type=Path, default=None, help="讀已下載的本機檔，不連網路（要同時給 --retrieved-url）")
    ap.add_argument("--media-type", default=None, help="省略時由回應的 Content-Type 或檔名判斷")
    ap.add_argument("--published-on", default=None, help="公告日或發布日（ISO 日期）")
    ap.add_argument("--document-number", default=None, help="發文字號或公告字號，照官方文件抄錄")
    ap.add_argument("--version", default=None, help="資料集版本或文件版次")
    ap.add_argument("--license", default="unknown", help="授權或散布條件；不確定時是 unknown（repo 公開前要先確認）")
    ap.add_argument("--note", default=None)
    ap.add_argument("--unversioned-web-table", action="store_true",
                    help="沒有版次的官方網頁表格例外（dataset 類 HTML）：三個版本欄位可以都是 null，note 補記 Last-Modified 與內容 hash")
    ap.add_argument("--last-modified", default=None, help="--file 模式下照抄下載時伺服器回應的 Last-Modified")
    ap.add_argument("--recheck", action="store_true", help="重新抓取既有快照，以排除後的內容 hash 比對是否改變；不寫任何檔")
    args = sys.argv[1:] if argv is None else argv
    if "--recheck" in args:                       # 只需要 source_id（與可選的 --retrieved-url），其他欄位不必給
        r = argparse.ArgumentParser(description="重新抓取既有的 bronze/<source_id>/，比對排除後的內容 hash（不寫檔）")
        r.add_argument("source_id")
        r.add_argument("--recheck", action="store_true")
        r.add_argument("--retrieved-url", default=None)
        ra, _ = r.parse_known_args(args)
        raise SystemExit(0 if recheck(ra.source_id, ra.retrieved_url) else 1)
    a = ap.parse_args(argv)
    validate(a)

    out = bronze_dir(a.source_id)
    if out.exists():
        fail(f"{out} 已存在；bronze 只進不改。新版本另開 bronze/{a.source_id}-<版本或日期>/")

    retrieved = a.retrieved_url or a.url
    last_modified = a.last_modified
    if a.file is not None:
        raw = a.file.read_bytes()
        media = _media(a.media_type) or _media(mimetypes.guess_type(a.file.name)[0])
        ext = a.file.suffix.lstrip(".").lower() or EXTENSIONS.get(media or "")
    elif a.unversioned_web_table:
        raw, ctype, last_modified = download_meta(retrieved)
        media = _media(a.media_type) or _media(ctype)
        ext = EXTENSIONS.get(media or "")
    else:
        raw, ctype = download(retrieved)
        media = _media(a.media_type) or _media(ctype)
        ext = EXTENSIONS.get(media or "")
    if not media or not ext:
        fail(f"無法判斷檔案類型（media_type {media!r}）；用 --media-type 指定")
    note = a.note
    if a.unversioned_web_table:
        if media not in UNVERSIONED_MEDIA:
            fail(f"--unversioned-web-table 只適用於 HTML 表格，這個檔是 {media}")
        note = f"{a.note.rstrip('。')}。{unversioned_note(raw, last_modified)}"

    prov = {
        "source_type": a.type, "source_id": a.source_id, "title": a.title, "publisher": a.publisher,
        "url": a.url, "retrieved_url": retrieved,
        "published_on": a.published_on, "document_number": a.document_number, "version": a.version,
        "source_file": f"source.{ext}", "media_type": media, "sha256": hashlib.sha256(raw).hexdigest(),
        "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "fetched_by": f"tools/fetch_source.py {TOOL_VERSION}", "license": a.license, "note": note,
    }
    assert list(prov) == list(PROVENANCE_FIELDS)

    # 先寫到同層的暫存目錄，完成後才改名：中途失敗不會留下寫了一半的 bronze 目錄
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{a.source_id}.", dir=out.parent))
    try:
        (tmp / prov["source_file"]).write_bytes(raw)
        (tmp / "provenance.json").write_text(_dump(prov), encoding="utf-8")
        if out.exists():
            fail(f"{out} 已存在")
        os.rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(_dump(prov), end="")
    return prov


if __name__ == "__main__":
    main()
