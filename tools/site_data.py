"""每個 release 匯出 site-data bundle：網站與 release 報告唯一讀取的資料。

用法：python3 tools/site_data.py 2026Q4               寫 index/<release>/site-data/
      python3 tools/site_data.py 2026Q4 --check       重新產生並與已提交的逐 byte 比對，不一致即結束碼 1
      python3 tools/site_data.py --dataset laborcalc  該資料集（datasets.yaml）的最新 release
      python3 tools/site_data.py --catalog [--check]  寫（或比對）index/datasets.json：所有資料集與它們的 release

資料集：每個 release 屬於 datasets.yaml 的一個資料集，bundle 只含那個資料集的 family，不同資料集的 release
各自一包、互不影響。release 的 family 混了兩個資料集、或 index.json 的 index_name 不是該資料集的名稱，就拒絕產生。
index/datasets.json 是網站列出資料集用的目錄（格式見 tools/schemas/datasets_catalog.schema.json），登錄檔改了要重跑。

內容對應 pipeline 的每一層，每個數字都從產物讀，不手填（Decision Log 2026-08-24〈呈現用產物必須從 run artifact 產生〉）：
  laws.json      bronze：法規來源、鏡像 commit、抓取時間、sha256；silver/inventory 的盤點與複核統計、已編碼條項
  sources.json   bronze：非法規來源（公告、資料集）的 provenance 與用到它的 family（0.2.0 起）
                 laws.json 與 sources.json 只列這個 release 的 public 題集 lineage 用到的快照，不掃描整個 bronze/：
                 新增 bronze 快照不會改變沒用到它的 release
  families.json  silver：spec 版本與四個簽核 hash、簽核紀錄、法源（含 AI 審閱意見）、公式、適用範圍、分支、golden 與官方出處狀態；
                 gold：public 題集與 manifest 的位置與 hash
  results.json   eval：照抄 index/<release>/index.json 的分數（不重算），每個 run 附 log 路徑與 sha256（與 log 實檔核對；
                 repo 裡沒有原始 log 時——公開版不帶——改與逐題得分表 eval/scores/<release>/runs.json 記的 sha256 核對）
  manifest.json  schema 版本、上面四個檔的 sha256、讀過的每個來源檔的 sha256（沒有原始 log 時，log 那幾筆是得分表記的值，
                 與有 log 時相同，所以兩種情況產生同樣的 bytes）
格式見 tools/schemas/site_data.schema.json；寫出前先驗證。

不讀 gold/releases/*/private/，也不含 salt 以外的私有資訊：public manifest 的 salt 是公開值；
私有題集封存後只放它 manifest 的 hash，目前 private_set 一律為 null。
bundle 不含產生時間：同樣的輸入產生同樣的 bytes，--check 與 make reproduce 才能比對。index.json 每次 aggregate 都會變，
所以重跑 aggregate 後要重跑本工具（tests/test_site_data.py 會擋住忘了重跑的提交）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from family_reviews import HASH_FIELDS, family_hashes, family_status  # noqa: E402
import fetch_source  # noqa: E402  — bronze 快照的共用判讀（source_type、snapshot_file、check_snapshot）
import dataset_registry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = "0.2.0"   # 0.2.0：laws／sources 依 release 的 lineage 輸出、新增 sources.json。0.1.0：第一版
SCHEMA = Path(__file__).resolve().parent / "schemas" / "site_data.schema.json"
CATALOG_SCHEMA = Path(__file__).resolve().parent / "schemas" / "datasets_catalog.schema.json"
CATALOG_SCHEMA_VERSION = dataset_registry.CATALOG_SCHEMA_VERSION
CATALOG = "index/datasets.json"
OUTPUTS = ("laws.json", "sources.json", "families.json", "results.json")
CATEGORIES = ("computable", "lookup", "definitional", "discretionary", "delegated")


class Sources:
    """記下讀過的每個來源檔（repo 相對路徑 → sha256），寫進 manifest.json。"""

    def __init__(self, root: Path):
        self.root, self.seen = root, {}

    def path(self, rel: str) -> Path:
        p = self.root / rel
        self.seen[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return p

    def text(self, rel: str) -> str:
        return self.path(rel).read_text(encoding="utf-8")

    def json(self, rel: str):
        return json.loads(self.text(rel))

    def yaml(self, rel: str):
        return yaml.safe_load(self.text(rel))


def _plain(o):
    """YAML 讀進來的值轉成 JSON 可寫、與平台無關的形式：日期轉 ISO 字串（未加引號的 2026-10-12 會被 YAML 讀成 date），字串去頭尾空白。"""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, str):
        return o.strip()
    return o


# ---------------------------------------------------------------- bronze + silver/inventory

def release_lineage(src: Sources, release: str, family_ids: list[str]) -> dict[str, dict[str, dict[str, list[str]]]]:
    """{"laws": {pcode: {"families": […], "sha256": […]}}, "sources": {source_id: …}}：這個 release 的 public 題集
    manifest 記的 lineage（用到它的 family、各題集記的 bronze_sha256）。laws() 與 sources() 只輸出這裡列到的快照。"""
    used: dict[str, dict[str, dict[str, list[str]]]] = {"laws": {}, "sources": {}}
    for fid in family_ids:
        rel = f"gold/releases/{release}/public/{fid}.manifest.json"
        if not (src.root / rel).exists():
            continue
        lineage = src.json(rel)["lineage"]
        for kind in used:
            for key, rec in lineage.get(kind, {}).items():
                u = used[kind].setdefault(key, {"families": [], "sha256": []})
                u["families"].append(fid)
                u["sha256"].append(rec["bronze_sha256"])
    return used


def _snapshot(src: Sources, key: str, recorded: list[str], kind: str) -> dict:
    """讀 bronze/<key>/ 的 provenance 與快照檔，核對快照 sha256 = provenance = 題目 lineage 記的值。"""
    base = f"bronze/{key}"
    if not (src.root / base / "provenance.json").exists():
        raise ValueError(f"題目的 lineage 引用了 {base}，但 {base}/provenance.json 不存在")
    prov = src.json(f"{base}/provenance.json")
    if (fetch_source.source_type(prov) == "law") != (kind == "laws"):
        raise ValueError(f"{base} 的 source_type 是 {fetch_source.source_type(prov)}，卻列在 lineage 的 {kind}")
    snap = fetch_source.snapshot_file(Path(base), prov).as_posix()
    src.path(snap)                                              # 快照本身：sha256 必須等於 provenance 記的（原樣 bytes）
    if src.seen[snap] != prov["sha256"]:
        raise ValueError(f"{snap} 的 sha256 與 provenance.json 不符")
    if any(h != prov["sha256"] for h in recorded):
        raise ValueError(f"{base} 的 sha256 與題目 lineage 記的 bronze_sha256 不符：題集不是用這份快照產生的")
    return prov


def review_cutoff(src: Sources, release: str, family_ids: list[str]) -> str | None:
    """這個 release 的題集產生時，盤點複核的狀態截至哪一天：每個 family 取 hash 與題集 manifest 相同的簽核日期，
    再取所有 family 的最晚一天。題集一定在簽核之後產生，所以之後才寫進 triage 的複核不屬於這個 release。
    沒有任何相符的簽核時回 None（照現在的盤點）。"""
    dates = []
    for fid in family_ids:
        rel = f"gold/releases/{release}/public/{fid}.manifest.json"
        reviews_rel = f"silver/families/{fid}/reviews.yaml"
        if not (src.root / rel).exists() or not (src.root / reviews_rel).exists():
            continue
        man = src.json(rel)
        matching = [str(r["date"]) for r in (src.yaml(reviews_rel) or {}).get("reviews", [])
                    if r.get("verdict") == "approved" and all(r.get(k) == man.get(k) for k in HASH_FIELDS)]
        if matching:
            dates.append(max(matching))
    return max(dates) if dates else None


def _earlier_decisions(src: Sources, inv_dir: str, cutoff: str) -> dict[str, dict]:
    """reviews/*.yaml 裡日期不晚於截止日的複核，依檔名順序套用後每個條項最後的決定（與 triage.yaml 的套用順序相同）。"""
    last: dict[str, dict] = {}
    for p in sorted((src.root / inv_dir / "reviews").glob("*.yaml")):
        doc = src.yaml(f"{inv_dir}/reviews/{p.name}") or {}
        if str(doc.get("date", "")) > cutoff:
            continue
        for d in doc.get("decisions", []):
            last[d["unit_id"]] = d
    return last


def _as_of(u: dict, cutoff: str | None, earlier) -> dict:
    """截止日之後才複核的條項，還原成截止日當時的樣子：有更早的複核就用那次的決定，沒有就回到 pending；
    分類與 family 先還原成 override.from 記的原值，再套更早那次的 overridden。"""
    if cutoff is None or u.get("review") == "pending" or str(u.get("reviewed_at") or "") <= cutoff:
        return u
    before = {**u, **((u.get("override") or {}).get("from") or {}), "review": "pending"}
    prior = earlier().get(u["unit_id"])
    if prior:
        before["review"] = prior["verdict"]
        if prior["verdict"] == "overridden":
            before.update({k: prior[k] for k in ("category", "family") if k in prior})
    return before


def laws(src: Sources, used: dict[str, dict[str, list[str]]], cutoff: str | None = None) -> dict:
    """已出貨的 release 不因之後新增 family 或複核而改變：已編碼條文只列這個 release 的 family，
    複核數依 review_cutoff() 還原到題集產生時。"""
    out = []
    for pcode in sorted(used):
        prov = _snapshot(src, pcode, used[pcode]["sha256"], "laws")
        entry = {"pcode": pcode, **{k: prov.get(k) for k in ("law_name", "law_url", "last_amended", "source", "source_file",
                                                             "mirror_commit", "mirror_update_date", "fetched_at", "sha256")},
                 "inventory": None}
        inv_dir = f"silver/inventory/{pcode}"
        if (src.root / inv_dir / "inventory.json").exists() and not (src.root / inv_dir / "triage.yaml").exists():
            raise ValueError(f"{inv_dir} 有 inventory.json 但沒有 triage.yaml：先跑 tools/triage.py {pcode} --stage merge")
        if (src.root / inv_dir / "triage.yaml").exists():
            triage = src.yaml(f"{inv_dir}/triage.yaml")
            fam_doc = src.yaml(f"{inv_dir}/families.yaml") if (src.root / inv_dir / "families.yaml").exists() else {"families": []}
            cache: dict = {}
            earlier = lambda: cache.setdefault("d", _earlier_decisions(src, inv_dir, cutoff))  # noqa: E731 — 只在需要還原時才讀 reviews/
            units = [u for u in (_as_of(u, cutoff, earlier) for u in triage["units"]) if u["category"] != "deleted"]
            cat = Counter(u["category"] for u in units)
            release_families = set(used[pcode]["families"])
            encoded = [{"unit_id": u["unit_id"], "family": f["id"], "coverage": u["coverage"], "note": u.get("note")}
                       for f in fam_doc["families"] if f["id"] in release_families for u in f["units"]]
            entry["inventory"] = {
                "triage_version": triage.get("triage_version"),
                "articles": len({u["article"] for u in units}),
                "units": len(units),
                "by_category": {c: cat.get(c, 0) for c in sorted(set(CATEGORIES) | set(cat))},
                "review": dict(sorted(Counter(u["review"] for u in units).items())),
                "conflicts_open": sum(1 for u in units if u["conflicts"] and u["review"] == "pending"),
                "encoded_units": encoded,
                "page": f"site/rules/laws/coverage_{pcode}.html",
            }
        out.append(entry)
    return {"laws": out}


def sources(src: Sources, used: dict[str, dict[str, list[str]]]) -> dict:
    """非法規來源：provenance 的 16 個欄位照抄，另加用到它的 family。"""
    out = []
    for sid in sorted(used):
        prov = _snapshot(src, sid, used[sid]["sha256"], "sources")
        out.append({k: prov.get(k) for k in fetch_source.PROVENANCE_FIELDS} | {"families": sorted(set(used[sid]["families"]))})
    return {"sources": out}


# ---------------------------------------------------------------- silver/families + gold

def _exclude(e) -> dict:
    return e if isinstance(e, dict) else {"item": e, "reason": None}


def _ai(src: Sources, fid: str, spec_sha: str):
    rel = f"silver/families/{fid}/ai_review.json"
    if not (src.root / rel).exists():
        return None, {}
    ai = src.json(rel)
    meta = {"model": ai["model"], "reviewed_at": ai["reviewed_at"], "note": ai["note"],
            "spec_sha256": ai["spec_sha256"], "current": ai["spec_sha256"] == spec_sha}
    return meta, ai["entries"]


def _public_set(src: Sources, release: str, fid: str, hashes: dict):
    rel = f"gold/releases/{release}/public/{fid}"
    if not (src.root / f"{rel}.manifest.json").exists():
        return None
    man = src.json(f"{rel}.manifest.json")
    src.path(f"{rel}.jsonl")
    return {"path": f"{rel}.jsonl", "sha256": src.seen[f"{rel}.jsonl"],
            "manifest_path": f"{rel}.manifest.json", "manifest_sha256": src.seen[f"{rel}.manifest.json"],
            **{k: man[k] for k in ("family_version", "family_status", "generator_version", "n", "rejected", "as_of", "salt")},
            "hashes": {k: man[k] for k in HASH_FIELDS},
            "matches_current_files": all(man[k] == hashes[k] for k in HASH_FIELDS),
            "lineage": man["lineage"]}


# spec 的每個頂層欄位不是匯出就是刻意不匯出（tests/test_site_data.py 會擋住新欄位被默默丟掉）。
# 匯出的欄位整個照抄（日期轉 ISO 字串）：網站要能畫出和規則卡一樣的內容，包括公式的 mathml、法源的 verify: pending。
SPEC_EXPORTED = {  # spec 欄位 → bundle 欄位
    "name": "name", "version": "version", "tier": "tier", "language": "language", "curated_at": "curated_at", "curator": "curator",
    "summary": "summary", "legal_basis": "legal_basis", "scope": "scope", "parameters": "parameters", "answer": "answer",
    "rate_tables": "rate_tables", "rounding": "rounding", "formulas": "formulas", "formula_legend": "formula_legend",
    "branches": "branches", "boundary_values": "boundary_values", "golden_cases": "golden", "open_questions": "open_questions",
    "changelog": "changelog",
}
SPEC_OMITTED = {
    "id": "已是 family 條目的 id",
    "implementation": "rule.py／rule.js 的檔名；rule 的內容以 hashes.rule_sha256 代表",
    "bronze_sources": "非法規來源以題集 lineage 為準，列在 sources.json",
}


def families(src: Sources, release: str, family_ids: list[str]) -> dict:
    out = []
    for fid in family_ids:
        base = f"silver/families/{fid}"
        spec = src.yaml(f"{base}/spec.yaml")
        fdir = src.root / base
        for name in ("rule.py", "templates.yaml", "sampler.py"):
            src.path(f"{base}/{name}")
        if (fdir / "reviews.yaml").exists():
            src.path(f"{base}/reviews.yaml")
        unknown = set(spec) - set(SPEC_EXPORTED) - set(SPEC_OMITTED)
        if unknown:
            raise ValueError(f"{base}/spec.yaml 有 site_data.py 不認得的欄位：{sorted(unknown)}（加進 SPEC_EXPORTED 或 SPEC_OMITTED）")
        hashes = family_hashes(fdir)
        st = family_status(fdir)
        ai_meta, ai_entries = _ai(src, fid, hashes["spec_sha256"])
        entry = {"id": fid, "status": st["status"], "hashes": hashes,
                 "reviews": [_plain({k: r.get(k) for k in ("reviewer", "role", "date", "verdict", "notes", "current", *HASH_FIELDS)})
                             for r in st["reviews"]],
                 "card": f"site/rules/families/{fid}.html", "ai_review": ai_meta}
        entry |= {dst: _plain(spec.get(key)) for key, dst in SPEC_EXPORTED.items()}
        entry["legal_basis"] = [b | {"ai_review": ({"verdict": ai_entries[b["id"]]["verdict"], "reason": ai_entries[b["id"]]["reason"]}
                                                   if b["id"] in ai_entries else None)} for b in entry["legal_basis"]]
        entry["scope"] = entry["scope"] | {"excludes": [_exclude(e) for e in entry["scope"].get("excludes", [])]}
        entry["open_questions"] = entry["open_questions"] or []
        entry["changelog"] = entry["changelog"] or []
        entry["golden_status_counts"] = dict(sorted(Counter(g.get("status") for g in entry["golden"]).items()))
        entry["public_set"] = _public_set(src, release, fid, hashes)
        entry["private_set"] = None                              # 私有題集封存後只放它 manifest 的 hash
        out.append(entry)
    return {"families": out}


# ---------------------------------------------------------------- eval（照抄 index.json）

RUN_FIELDS = ("n", "expected_n", "correct", "n_scored", "correct_scored", "accuracy", "n_control", "correct_control",
              "control_pass", "complete", "tier", "answer_mode", "by_branch", "log_file", "log_sha256", "run_id", "model", "fake")


def _table_log_hashes(root: Path, release: str) -> dict[str, str]:
    """逐題得分表（eval/score_table.py）記的 log 檔名 → sha256；沒有得分表時是空的。"""
    p = root / "eval" / "scores" / release / "runs.json"
    if not p.exists():
        return {}
    return {r["log_file"]: r["log_sha256"] for r in json.loads(p.read_text(encoding="utf-8"))["runs"]}


def results(src: Sources, release: str, ix: dict) -> dict:
    targets = []
    table = None      # 沒有原始 log 時才讀
    for name in sorted(ix["targets"]):
        t = ix["targets"][name]
        parts = name.split("@")
        splits = {}
        for split, s in t["splits"].items():
            runs = []
            for fam in sorted(s.get("families", {})):
                r = s["families"][fam]
                withheld = split != "public"         # 私有題集的 log 含私有題目，不進 repo：不開檔，只照抄 index 記的 sha256
                log_path = None if withheld else f"eval/logs/{release}/{r['log_file']}"
                if log_path and (src.root / log_path).exists():
                    if src.path(log_path) and src.seen[log_path] != r["log_sha256"]:
                        raise ValueError(f"{name} {fam}：{log_path} 的 sha256 與 index.json 的 log_sha256 不符")
                elif log_path:
                    # 公開版不帶原始 log：sha256 取自逐題得分表，同樣要等於 index 記的值。manifest 的 sources 照記這個值，
                    # 所以有沒有原始 log，bundle 都是同樣的 bytes
                    table = _table_log_hashes(src.root, release) if table is None else table
                    if r["log_file"] not in table:
                        raise ValueError(f"{name} {fam}：index 記的 log 不存在：{log_path}，"
                                         f"逐題得分表 eval/scores/{release}/runs.json 也沒有這份紀錄")
                    if table[r["log_file"]] != r["log_sha256"]:
                        raise ValueError(f"{name} {fam}：得分表記的 {r['log_file']} sha256 與 index.json 的 log_sha256 不符")
                    src.seen[log_path] = r["log_sha256"]
                mm = r.get("spec_mismatch")
                runs.append({"family": fam, **{k: r.get(k) for k in RUN_FIELDS}, "log_path": log_path, "log_withheld": withheld,
                             "stale": mm is not None, "stale_fields": (mm or {}).get("differs", [])})
            splits[split] = {k: s.get(k) for k in ("score", "status", "reason", "control_pass", "n_control", "family_scores")} | {"runs": runs}
        targets.append({"target": name, "model": parts[0], "quantization": parts[1] if len(parts) > 1 else None,
                        "hardware": parts[2] if len(parts) > 2 else None, "answer_mode": parts[3] if len(parts) > 3 else None,
                        "is_fake": name.startswith("fake-"), "splits": splits, "public_private_gap": t.get("public_private_gap")})
    return {"release": release, **{k: ix[k] for k in ("index_name", "methodology_version", "mode", "weights", "headline_tiers",
                                                       "families", "release_blockers", "notes")},
            "targets": targets}


# ---------------------------------------------------------------- 組裝、驗證、寫出

def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _validate(name: str, obj) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name.removesuffix('.json')}"}
    jsonschema.validate(obj, sub)


def render(root: Path, release: str) -> dict[str, bytes]:
    root = Path(root)
    src = Sources(root)
    ix = src.json(f"index/{release}/index.json")
    # 登錄檔只用來核對歸屬，不記進 manifest 的 sources：它不決定 bundle 的任何內容
    dataset = dataset_registry.load(root / "datasets.yaml").resolve_release(release, ix["families"])
    if ix["index_name"] != dataset.name:
        raise ValueError(f"index/{release}/index.json 的 index_name 是 {ix['index_name']}，但 release 屬於 {dataset.name}："
                         f"重跑 python3 eval/aggregate.py --release {release}")
    used = release_lineage(src, release, ix["families"])
    docs = {"laws.json": laws(src, used["laws"], review_cutoff(src, release, ix["families"])), "sources.json": sources(src, used["sources"]),
            "families.json": families(src, release, ix["families"]), "results.json": results(src, release, ix)}
    blobs = {}
    for name, obj in docs.items():
        _validate(name, obj)
        blobs[name] = _dump(obj)
    manifest = {"schema_version": SCHEMA_VERSION, "release": release, "mode": ix["mode"],
                "methodology_version": ix["methodology_version"],
                "files": {n: hashlib.sha256(blobs[n]).hexdigest() for n in OUTPUTS},
                "sources": dict(sorted(src.seen.items()))}
    if any("/private/" in s for s in manifest["sources"]):
        raise ValueError("site-data 不得讀 private 題集")
    _validate("manifest.json", manifest)
    return {"manifest.json": _dump(manifest), **blobs}


def build(root: Path, release: str, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, blob in render(root, release).items():
        (out_dir / name).write_bytes(blob)
        paths.append(out_dir / name)
    return paths


def check(root: Path, release: str) -> list[str]:
    """重新產生並與已提交的 index/<release>/site-data/ 比對；回傳不一致的說明（空 list = 一致）。"""
    committed = Path(root) / "index" / release / "site-data"
    try:
        fresh = render(root, release)
    except Exception as e:  # noqa: BLE001 — 缺檔、格式錯都要變成一行說明，不是 traceback
        return [f"無法重新產生：{type(e).__name__}: {e}"]
    problems = []
    for name, blob in fresh.items():
        p = committed / name
        if not p.exists():
            problems.append(f"{name}：已提交的 bundle 裡沒有這個檔")
        elif p.read_bytes() != blob:
            problems.append(f"{name}：與重新產生的內容不同（重跑 python3 tools/site_data.py {release}）")
    extra = sorted(p.name for p in committed.glob("*") if p.name not in fresh) if committed.exists() else []
    return problems + [f"{n}：不是本工具產生的檔" for n in extra]


# ---------------------------------------------------------------- index/datasets.json（所有資料集的目錄）

def render_catalog(root: Path) -> bytes:
    """datasets.yaml 的公開部分，加上每個 release 的 site-data 位置（還沒產生的為 null）。"""
    blob = dataset_registry.catalog_bytes(root)      # 內容由登錄模組產生（公開匯出也用同一個函式），這裡另做 schema 驗證
    jsonschema.validate(json.loads(blob), json.loads(CATALOG_SCHEMA.read_text(encoding="utf-8")))
    return blob


def check_catalog(root: Path) -> list[str]:
    p = Path(root) / CATALOG
    try:
        fresh = render_catalog(root)
    except Exception as e:  # noqa: BLE001
        return [f"{CATALOG}：無法重新產生：{type(e).__name__}: {e}"]
    if not p.exists():
        return [f"{CATALOG}：還沒產生（python3 tools/site_data.py --catalog）"]
    return [] if p.read_bytes() == fresh else [f"{CATALOG}：與重新產生的內容不同（python3 tools/site_data.py --catalog）"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("release", nargs="?")
    ap.add_argument("--dataset", help="用該資料集（datasets.yaml）的最新 release")
    ap.add_argument("--catalog", action="store_true", help=f"寫 {CATALOG}（所有資料集的目錄）")
    ap.add_argument("--check", action="store_true", help="重新產生並與已提交的比對，不寫檔")
    a = ap.parse_args(argv)
    root = ROOT
    if a.catalog:
        if a.check:
            problems = check_catalog(root)
            for p in problems:
                print(f"✗ {p}")
            if not problems:
                print(f"✓ {CATALOG} 與重新產生的一致")
            return 1 if problems else 0
        (root / CATALOG).write_bytes(render_catalog(root))
        print(f"→ {CATALOG}")
        return 0
    if a.dataset:
        if a.release:
            ap.error("release 與 --dataset 只能給一個")
        a.release = dataset_registry.load(root / "datasets.yaml").get(a.dataset).latest_release
        if not a.release:
            ap.error(f"資料集 {a.dataset} 還沒有 release")
    if not a.release:
        ap.error("需要 release、--dataset 或 --catalog")
    if a.check:
        problems = check(root, a.release)
        for p in problems:
            print(f"✗ {p}")
        if not problems:
            print(f"✓ index/{a.release}/site-data 與重新產生的一致")
        return 1 if problems else 0
    for p in build(root, a.release, root / "index" / a.release / "site-data"):
        print(f"→ {p.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
