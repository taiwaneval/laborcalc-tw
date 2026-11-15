"""Family 簽核：紀錄在 silver/families/<id>/reviews.yaml，不在 spec.yaml（2026-10-10 起）。

為什麼不寫在 spec.yaml：題目的 lineage 以 spec.yaml 整檔的 sha256 為準。把簽核寫進 spec 會改變 spec 自己的 hash，
簽核紀錄永遠對不上它簽的版本；而且每次簽核都會讓既有 eval log 被 aggregate 判成「舊 spec 跑的」。
所以 spec.yaml 的 status／review_log 欄位作廢，family 狀態只由這裡推得。

簽核對象是決定題目與答案的四個 family 檔，各取整檔 sha256，與 gold/generate.py 寫進每題 lineage 的 hash 同一套算法：
  spec_sha256       spec.yaml
  rule_sha256       spec.implementation.python 指向的檔案（rule.py）
  templates_sha256  templates.yaml（題幹）
  sampler_sha256    sampler.py（抽樣與題幹欄位）
任一檔改動，舊簽核自動不再對應，family 回到 draft，必須重新簽核。曾有一次只改模板就改了 108 題的題幹，所以模板與 sampler 也要簽。

不在簽核對象裡的檔：hypotheses.py（只用於錯答歸因與 naive 假 solver）、rule.js（規則卡試算器；test_agreement 保證與 rule.py 一致）、
共用的產生器程式（gold/generate.py、tools/numerals.py、tools/sampling.py）。產生器由 GENERATOR_VERSION 代表：它進 lineage，
aggregate 的版本檢查會比對；改動產生器而改變輸出時必須升版，「已提交題集要能逐 byte 重生」的測試會擋住沒升版的改動。
一次產生器重構不應讓每個 family 的簽核都失效，所以它不進 family 簽核。

reviews.yaml 只能人工寫（repo 規則：人工決定只寫進 reviews 類檔案，LLM 不得代簽）。格式：

  reviews:
    - reviewer: Calvin Ku          # 具名審閱者
      role: owner                  # 審閱者角色
      date: "2026-10-12"           # ISO 日期
      spec_sha256: <64 位 hex>       # 以下四個 hash 都是規則卡上印的，整串照抄
      rule_sha256: <64 位 hex>
      templates_sha256: <64 位 hex>
      sampler_sha256: <64 位 hex>
      verdict: approved            # approved｜changes_requested
      notes: 逐條對照法源；試算器打過官方案例   # 選填

紀錄只增不改。同一組 hash 有多筆時，以日期最晚的一筆為準（同日以檔案中較後者為準）；
最新一筆是 approved 才算 reviewed。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

import yaml

REVIEWS_FILE = "reviews.yaml"
VERDICTS = ("approved", "changes_requested")
HASH_FIELDS = ("spec_sha256", "rule_sha256", "templates_sha256", "sampler_sha256")
REQUIRED = ("reviewer", "role", "date", *HASH_FIELDS, "verdict")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def family_hashes(fdir: Path) -> dict[str, str]:
    """四個簽核檔的整檔 sha256，鍵依 HASH_FIELDS 的順序。"""
    fdir = Path(fdir)
    spec_path = fdir / "spec.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    return {
        "spec_sha256": _sha256(spec_path),
        "rule_sha256": _sha256(fdir / spec["implementation"]["python"]),
        "templates_sha256": _sha256(fdir / "templates.yaml"),
        "sampler_sha256": _sha256(fdir / "sampler.py"),
    }


def _validate(i: int, e) -> dict:
    where = f"{REVIEWS_FILE} 第 {i + 1} 筆"
    if not isinstance(e, dict):
        raise ValueError(f"{where}：每筆必須是 mapping")
    for k in REQUIRED:
        if k not in e or e[k] is None or (isinstance(e[k], str) and not e[k].strip()):
            raise ValueError(f"{where}：缺少 {k}")
    d = e["date"]
    if isinstance(d, date):
        d = d.isoformat()
    try:
        date.fromisoformat(str(d))
    except ValueError:
        raise ValueError(f"{where}：date 必須是 YYYY-MM-DD，收到 {e['date']!r}") from None
    for k in HASH_FIELDS:
        if not _HEX64.match(str(e[k])):
            raise ValueError(f"{where}：{k} 必須是完整的 64 位小寫 hex（照抄規則卡），收到 {str(e[k])[:16]!r}")
    if e["verdict"] not in VERDICTS:
        raise ValueError(f"{where}：verdict 只能是 {'｜'.join(VERDICTS)}，收到 {e['verdict']!r}")
    out = dict(e)
    out["date"] = str(d)
    out["_order"] = i
    return out


def load_reviews(fdir: Path) -> list[dict]:
    """讀 reviews.yaml 並驗證每筆。沒有檔案 → []。格式錯誤 → ValueError，訊息指出第幾筆、哪個欄位。"""
    p = Path(fdir) / REVIEWS_FILE
    if not p.exists():
        return []
    doc = yaml.safe_load(p.read_text()) or {}
    entries = doc.get("reviews") or []
    if not isinstance(entries, list):
        raise ValueError(f"{REVIEWS_FILE}：reviews 必須是 list")
    return [_validate(i, e) for i, e in enumerate(entries)]


def _public(e: dict | None) -> dict | None:
    return None if e is None else {k: v for k, v in e.items() if not k.startswith("_")}


def _key(hashes: dict) -> tuple:
    return tuple(hashes.get(k) for k in HASH_FIELDS)


def status_for(fdir: Path, hashes: dict) -> dict:
    """這組 hash 有沒有被簽核。{"status": "reviewed"|"draft", "review": 生效的那筆或 None}。

    hashes 可以直接傳 manifest 或題目：只看 HASH_FIELDS，其他欄位忽略；缺任何一個 hash 就不可能對上簽核。
    aggregate 用 manifest 上的 hash 問（產生這批題目的版本有沒有簽過），不是用目前的檔案。"""
    want = _key(hashes)
    if None in want:
        return {"status": "draft", "review": None}
    matching = [e for e in load_reviews(fdir) if _key(e) == want]
    if not matching:
        return {"status": "draft", "review": None}
    latest = max(matching, key=lambda e: (e["date"], e["_order"]))
    return {"status": "reviewed" if latest["verdict"] == "approved" else "draft", "review": _public(latest)}


def family_status(fdir: Path) -> dict:
    """目前檔案的狀態：status_for(目前的四個 hash)，另附這四個 hash 與全部簽核紀錄（規則卡用）。"""
    hashes = family_hashes(fdir)
    st = status_for(fdir, hashes)
    reviews = [_public(e) | {"current": _key(e) == _key(hashes)} for e in load_reviews(fdir)]
    return st | hashes | {"reviews": reviews}
