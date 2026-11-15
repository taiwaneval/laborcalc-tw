"""抓取一部法規的開放資料快照。

來源優先序：
  1. 全國法規資料庫「法規資料開放」批次檔（law.moj.gov.tw）— 正式來源，部署時用
  2. kong0107/mojLawSplitJSON — 同一份開放資料的 GitHub 鏡像，按法規拆成單檔，開發期用

兩者欄位相同（法規名稱／法規網址／最新異動日期／沿革內容／法規內容）。
快照落地為 bronze/<pcode>/source.json，並記錄 provenance（來源、取得時間、鏡像 commit、檔案 sha256）。
source.json 存來源檔的原樣 bytes，不重新排版：provenance 的 sha256 就是 repo 裡這個檔的 sha256，
任何人都能直接驗證，也能和鏡像 commit 上的原檔比對（tools/reproduce.py）。

用法：python3 tools/fetch_law.py N0030001 --mirror /path/to/mojlawsplitjson
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from paths import bronze_dir


def fetch_from_mirror(pcode: str, mirror: Path) -> tuple[bytes, dict]:
    """(來源檔原樣 bytes, provenance)。"""
    src = mirror / "FalVMingLing" / f"{pcode}.json"
    raw = src.read_bytes()
    commit = subprocess.run(["git", "-C", str(mirror), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    update_date = (mirror / "UpdateDate.txt").read_text().strip()
    prov = {
        "source": "kong0107/mojLawSplitJSON (mirror of law.moj.gov.tw open data)",
        "source_file": f"FalVMingLing/{pcode}.json",
        "mirror_commit": commit,
        "mirror_update_date": update_date,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return raw, prov


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcode")
    ap.add_argument("--mirror", type=Path, required=True)
    a = ap.parse_args(argv)

    raw, prov = fetch_from_mirror(a.pcode, a.mirror)
    law = json.loads(raw)
    prov["fetched_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    prov["law_name"] = law["法規名稱"]
    prov["law_url"] = law["法規網址"]
    prov["last_amended"] = law["最新異動日期"]

    out = bronze_dir(a.pcode)
    out.mkdir(parents=True, exist_ok=True)
    (out / "source.json").write_bytes(raw)   # 原樣保存，sha256 = prov["sha256"]
    (out / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1))
    print(json.dumps(prov, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
