"""logs → 指數。公開的聚合程式：預設權重在 datasets.yaml（每個資料集的 default_weights），任何人可 fork 改權重重算。

輸入：eval/logs/<release>/*.eval（Inspect AI log），或逐題得分表 eval/scores/<release>/（eval/score_table.py 由 log 產生，
      不含模型作答原文）。有 log 時由 log 計算，同時有得分表就比對兩者，不一致即失敗、不寫檔；沒有 log（公開版不帶原始
      紀錄）時只讀得分表，算出的 index 與由 log 算出的相同。--source logs｜scores 可指定只用其中一個。
輸出：index/<release>/index.json  ＋  index/<release>/items.csv（每題每 target 的對錯，供重算）

規則（對應 TCRI v0.1 methodology 第 6、8、11 點，與 2026-10-08 修正）：
- 排名單位是 evaluation_target（model@quant@hardware），不是 model 名稱。同 target 多次同版、完整的 success run 視為重複執行：family 分數取平均，repeats 附每次的值與 min／max；
  release 需要每個模型 target 至少 MIN_REPEATS 次（目前 1；fake-* 除外）。items.csv 用最新那一次。
- 固定分母：一個 (target, family, split) 的作答數必須等於 gold manifest 的 n；少了就標 incomplete，不丟題、不補 0、不重加權，該 target 不出 aggregate。
  作答數照題目算：同一題因 retry_on_error 留下多筆 sample 時只算一題（有分數的那次優先，都沒有分數就算答錯）。
- Tier 1 開放數值題沒有 random baseline，family 分數 = raw accuracy。
- headline 只收 tier ∈ HEADLINE_TIERS 的 family；family 等權（datasets.yaml 該資料集的 default_weights 可覆寫）。
- 資料集：每個 release 屬於 datasets.yaml 的一個資料集；index_name、權重從那裡讀。release 的 family
  混了兩個資料集、或有 family 沒登記在任何資料集，兩種模式都拒絕執行、不寫檔；release 名稱沒登記則是 release 阻擋項。
- public 與 private 分開算、分開報；兩者都有時另報 gap。
- 每個 family 的 lineage（版本、spec/rule/bronze hash、triage 版本）從 manifest pin 進 index.json。
- 對照題（spec.yaml branches 標 `control: true`，如 overtime_pay/W0「無加班應答 0」）不進 accuracy：
  family 的 accuracy 只算非 control 分支（n_scored），另報 control_pass（n_control）。
  對照題抓的是「亂套規則」（有加班就加成、沒加班也給錢），答對不證明會算；混進分母會把 0 分模型抬成 5%。
  分母固定規則不變：completeness 仍看全部 n（含 control）。
- 版本檢查：log 裡每題的 VERSION_FIELDS（family_version、spec／rule／templates／sampler 四個 sha256、generator_version）
  必須等於當期 manifest；不等就在 stdout 警告「log 與當期題集不是同一版」，列出不同的欄位，並在 index 的 family 記錄標
  spec_mismatch（欄名沿用）。preview 模式只警告不剔除：分數仍算，但應重跑；release 模式直接擋下（見下）。
  templates 與 sampler 是後來加的：曾有一次只改模板就改了題幹，舊版檢查抓不到。
- family 狀態：pin 的 family_status 由 silver/families/<id>/reviews.yaml 推得——manifest 上的四個 hash
  有對應的 approved 簽核才是 reviewed。manifest 的 family_status 只是產生題目當時的快照。
- 兩種模式（--mode）：preview（預設）照寫 index，並在 index.json 的 release_blockers 列出擋住 release 的項目；
  release 遇到任何阻擋項就失敗、不寫任何檔案。阻擋項：pin 住的 family 沒有對應簽核；已簽核但 manifest 的 family_status
  還是簽核前的快照（要重生 gold）；有 log 與當期題集不是同一版；模型 target 的同版完整 run 不到 MIN_REPEATS 次；
  模型 target 在它跑過的 split 上缺任何一個 family 的 log（fake-* 除外）。
- 量化軸（2026-10-09 討論後定的規則）：target 的 quantization 欄位記的是「實際被服務的組態」，不是模型的理想型。
  同一模型不要求每個精度都有列；本機跑不了的組態就沒有那一列，不估、不補。量化退化曲線（4-bit／8-bit／bf16 三點）
  只在小模型上做，當作一次性的校準研究，回答「量化對這類規則計算題影響多大」，服務整個指數，不是每個模型各一條。
  大模型要未量化參考點時走 API 服務，hardware 欄位填 provider（如 @bf16@together、@ollama-cloud），index 頁要標示該列的硬體不在本機。
  第一組校準數據：gemma-4-e4b-it 的 optiq4／8bit／bf16 direct 都在地板（實算 1/190），量化不是它 0 分的原因。
- answer_mode（direct／cot）是評測層開關，從 log 的 task metadata 讀出並寫進 index；它是 target identity 的一部分，
  命名慣例 model@quant@hardware@direct｜@cot，不同 answer_mode 的 run 不合併（target 字串不同即不合併）。

用法：python3 eval/aggregate.py --release 2026Q4 [--mode preview|release] [--source auto|logs|scores]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from inspect_ai.log import read_eval_log

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "eval" / "logs"
RELEASES = ROOT / "gold" / "releases"
INDEX = ROOT / "index"
FAMILIES = ROOT / "silver" / "families"

sys.path.insert(0, str(ROOT / "tools"))
from family_reviews import HASH_FIELDS, status_for  # noqa: E402
import dataset_registry  # noqa: E402
sys.path.insert(0, str(ROOT / "eval"))
import score_table  # noqa: E402
from score_table import ScoreTableError  # noqa: E402

REGISTRY = ROOT / "datasets.yaml"
SCORES = ROOT / "eval" / "scores"       # 逐題得分表（eval/score_table.py）：沒有原始紀錄時的輸入
SOURCES = ("auto", "logs", "scores")

METHODOLOGY_VERSION = "0.2.0-draft"   # 2026-10-08 修正後第一個草稿版本
HEADLINE_TIERS = {1}                  # Tier 2 另列，Tier 3 不進
# 資料集名稱（index_name）與預設權重在 datasets.yaml；原本的 BENCHMARK_NAME／DEFAULT_WEIGHTS 已移到那裡。
MIN_FAMILIES_FOR_HEADLINE = 1         # v1 只有一個 family；正式 release 前應提高
MODES = ("preview", "release")
# log 的題目必須和當期 manifest 同一版：family 版本、四個簽核 hash（tools/family_reviews.py）與產生器版本
VERSION_FIELDS = score_table.VERSION_FIELDS
# 執行間變異（Decision Log）：release 時每個模型 target 的每個 family 至少要有這麼多次同版、完整的 run。
# fake-* solver 是決定性的假模型，不要求重複。
MIN_REPEATS = 1   # 2026-11-10 Calvin：成本考量每個 target 跑一次；多跑時仍平均並報範圍


class ReleaseGateError(Exception):
    """release 模式下有阻擋項。blockers 是給人看的字串清單，每項說明哪裡不對、怎麼修。"""

    def __init__(self, blockers: list[str]):
        super().__init__("release 關卡未通過：\n" + "\n".join(f"- {b}" for b in blockers))
        self.blockers = blockers


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def family_tier(family: str) -> int:
    return int(yaml.safe_load((FAMILIES / family / "spec.yaml").read_text())["tier"])


def family_control_branches(family: str) -> set[str]:
    """spec.yaml branches 中 control: true 的分支 id（對照題，不進 accuracy）。"""
    spec = yaml.safe_load((FAMILIES / family / "spec.yaml").read_text())
    return {b["id"] for b in spec.get("branches", []) if b.get("control")}


def load_manifests(release: str) -> dict[tuple[str, str], dict]:
    out = {}
    for split_dir in (RELEASES / release).iterdir():
        for mf in split_dir.glob("*.manifest.json"):
            m = json.loads(mf.read_text())
            out[(m["family"], m["split"])] = m
    return out


def log_version_mismatch(seen: set[tuple], mf: dict) -> dict | None:
    """seen：log 各題帶的 VERSION_FIELDS 值（tuple）。任一組與 manifest 不同 → 不一致紀錄，否則 None。

    紀錄含 log_has（各組不同的值）、manifest（當期值）、differs（哪些欄位不同，依 VERSION_FIELDS 順序）。"""
    want = tuple(mf.get(k) for k in VERSION_FIELDS)
    # 舊 log 的題目可能缺欄位（None）；排序時把 None 當空字串比，結果仍保留原值
    bad = sorted((t for t in seen if t != want), key=score_table.version_key)
    if not bad:
        return None
    differs = [k for i, k in enumerate(VERSION_FIELDS) if any(t[i] != want[i] for t in bad)]
    return {"log_has": [dict(zip(VERSION_FIELDS, t)) for t in bad],
            "manifest": dict(zip(VERSION_FIELDS, want)), "differs": differs}


def pool_runs(recs: list[dict], mf: dict | None) -> dict:
    """同一 (target, family, split) 的所有 success run → 一筆代表紀錄，附 repeats。

    重複執行只收「同版且完整」的 run：spec_mismatch 為 None，且 manifest 已知時 n 等於 manifest n。
    有這種 run 時，代表紀錄是其中最新的一次（items.csv、log_file 用它），repeats 列出每次的 accuracy、
    平均與 min／max；family 分數用平均。沒有時退回最新的一次（仍帶 spec_mismatch），repeats.n_runs = 0。"""
    recs = sorted(recs, key=lambda r: r["created"])
    current = [r for r in recs if not r["spec_mismatch"] and (mf is None or r["n"] == mf["n"])]
    base = dict((current or recs)[-1])
    accs = [r["correct_scored"] / r["n_scored"] for r in current if r["n_scored"]]
    base["repeats"] = {
        "n_runs": len(current), "accuracies": accs,
        "mean": sum(accs) / len(accs) if accs else None,
        "min": min(accs) if accs else None, "max": max(accs) if accs else None,
        "log_files": [r["log_file"] for r in current],
    }
    return base


USABLE_LOG_STATUSES = score_table.USABLE_LOG_STATUSES


def usable_log_status(status: str) -> bool:
    """error／cancelled 的 log 只要每題都有紀錄（分數或錯誤）就採用；started 是寫到一半的檔，不採用。完整性另由 n == manifest n 把關。"""
    return status in USABLE_LOG_STATUSES


def _error_kind(err) -> str | None:
    if err is None:
        return None
    msg = str(getattr(err, "message", err))
    return "timeout" if "Timeout" in msg else "cancelled" if "Cancelled" in msg else "error"


def sample_items(samples, controls: set) -> list[dict]:
    """每題一筆。沒有分數的題（逾時、被取消、API 錯誤）算答錯，error 欄記原因。

    Inspect 在 retry_on_error 時，同一題每次嘗試各留一筆 sample。題數照題目算，不照嘗試次數算：
    同一題只留一筆，有分數的那次優先；每次都沒有分數就留最後一次，算答錯。順序照題目第一次出現的位置。"""
    items: dict[str, dict] = {}
    for s in samples:
        sc = (s.scores or {}).get("integer_match")
        branch = s.metadata.get("branch")
        kept = items.get(s.id)
        if kept is not None and kept["error"] is None:
            continue    # 這一題已經有一次有分數的作答
        items[s.id] = {
            "item_id": s.id, "branch": branch, "control": branch in controls,
            "correct": bool(sc and sc.value == "C"), "answer": sc.answer if sc else None,
            "gold": s.target if isinstance(s.target, str) else s.target[0],
            "pair_id": s.metadata.get("pair_id"),
            "error": None if sc else (_error_kind(s.error) or "unscored"),
        }
    return list(items.values())


def log_runs(release: str) -> list[dict]:
    """eval/logs/<release>/ 裡每份可採用的紀錄，依檔名排序。每筆是計分需要的欄位（與 score_table.as_raw 同一個形式）：
    紀錄的檔名、sha256、狀態、run_id、建立時間、模型、target／family／split、各題帶的版本欄位，與每題一筆的 items。"""
    out = []
    for f in sorted((LOGS / release).glob("*.eval")):
        log = read_eval_log(f)
        if not usable_log_status(log.status):
            continue
        md = log.eval.metadata or {}
        out.append({
            "log_file": f.name, "log_sha256": sha256_file(f), "log_status": log.status, "run_id": log.eval.run_id,
            "created": log.eval.created, "model": log.eval.model, "fake": md.get("fake"),
            "answer_mode": md.get("answer_mode") or "direct",   # Patch B 之前的 log 沒有這欄，一律 direct
            "evaluation_target": md.get("evaluation_target", "unspecified"), "family": md["family"], "split": md["split"],
            "versions": sorted({tuple(s.metadata.get(k) for k in VERSION_FIELDS) for s in log.samples}, key=score_table.version_key),
            "items": sample_items(log.samples, family_control_branches(md["family"])),
        })
    return out


def table_runs(release: str) -> list[dict]:
    """同 log_runs，但從逐題得分表 eval/scores/<release>/ 讀；不需要原始紀錄。"""
    return [score_table.as_raw(r) for r in score_table.read(SCORES / release)]


def source_runs(release: str, source: str = "auto") -> list[dict]:
    """計分的輸入。auto：有原始紀錄就由紀錄算，同時有得分表時兩邊必須相同（不同就丟 ScoreTableError）；
    沒有紀錄、有得分表時讀得分表。logs／scores：只用指定的那一個，不比對。"""
    if source not in SOURCES:
        raise ValueError(f"source 只能是 {'｜'.join(SOURCES)}，收到 {source!r}")
    has_logs = any((LOGS / release).glob("*.eval"))
    has_table = score_table.exists(SCORES / release)
    if source == "scores" or (source == "auto" and not has_logs and has_table):
        return table_runs(release)                # 沒有表時 score_table.read 會說「沒有得分表」
    runs = log_runs(release)
    if source == "auto" and has_table:
        d = score_table.difference(table_runs(release), runs)
        if d:
            raise ScoreTableError(f"得分表 eval/scores/{release}/ 與評測紀錄不一致：{d}。"
                                  f"重跑 python3 eval/score_table.py --release {release}")
    return runs


def collect_runs(release: str, manifests: dict[tuple[str, str], dict] | None = None,
                 source: str = "auto") -> dict[tuple[str, str, str], dict]:
    """(target, family, split) → 代表紀錄（見 pool_runs）。manifests 給了就做 spec 漂移檢查。source 見 source_runs。"""
    all_runs: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    manifests = manifests or {}
    for raw in source_runs(release, source):
        key = (raw["evaluation_target"], raw["family"], raw["split"])
        items = raw["items"]
        scored = [i for i in items if not i["control"]]
        control = [i for i in items if i["control"]]
        # 版本檢查：每題帶的 VERSION_FIELDS 要等於當期 manifest
        mf = manifests.get((raw["family"], raw["split"]))
        spec_mismatch = None
        if mf:
            spec_mismatch = log_version_mismatch(set(raw["versions"]), mf)
            if spec_mismatch:
                print(f"WARNING: {raw['log_file']}: log 與當期題集不是同一版 — 不同的欄位：{', '.join(spec_mismatch['differs'])}；"
                      f"target={raw['evaluation_target']}，應重跑。")
        rec = {
            "log_file": raw["log_file"], "log_sha256": raw["log_sha256"], "run_id": raw["run_id"],
            "created": raw["created"], "model": raw["model"], "fake": raw["fake"],
            "answer_mode": raw["answer_mode"],
            "spec_mismatch": spec_mismatch,
            "n": len(items), "correct": sum(i["correct"] for i in items),
            "n_scored": len(scored), "correct_scored": sum(i["correct"] for i in scored),
            "n_control": len(control), "correct_control": sum(i["correct"] for i in control),
            "n_error": sum(1 for i in items if i["error"]), "log_status": raw["log_status"],
            "items": items,
        }
        all_runs[key].append(rec)
    return {key: pool_runs(recs, manifests.get((key[1], key[2]))) for key, recs in all_runs.items()}


def phrasing_consistency(items: list[dict]) -> dict | None:
    """配對題（同參數、不同模板）中，各題對錯一致的比例。只算整組都在的配對；沒有配對回 None。"""
    groups: dict[str, list[bool]] = defaultdict(list)
    for i in items:
        if i.get("pair_id"):
            groups[i["pair_id"]].append(i["correct"])
    full = [g for g in groups.values() if len(g) >= 2]
    if not full:
        return None
    both_correct = sum(all(g) for g in full)
    both_wrong = sum(not any(g) for g in full)
    consistent = both_correct + both_wrong
    return {"pairs": len(full), "consistent": consistent, "rate": consistent / len(full),
            "both_correct": both_correct, "both_wrong": both_wrong, "flipped": len(full) - consistent}


def summarize_split(fams: dict, families: list[str], weights: dict[str, float]) -> dict:
    """一個 (target, split) 的總分。任何 headline family 缺席或不完整 → unavailable、score=None；
    family_scores 無論 status 都附上（partial 顯示用），缺席的 family 為 None。"""
    headline = {f: v for f, v in fams.items() if v["tier"] in HEADLINE_TIERS}
    missing = [f for f in families if f not in headline]
    incomplete = [f for f, v in headline.items() if not v["complete"]]
    if missing or incomplete or len(headline) < MIN_FAMILIES_FOR_HEADLINE:
        score, status = None, "unavailable"
        reason = {"missing_families": missing, "incomplete_families": incomplete}
    else:
        wsum = sum(weights[f] for f in headline)
        score = sum(weights[f] * v["accuracy"] for f, v in headline.items()) / wsum
        status, reason = "ok", None
    n_ctl = sum(v["n_control"] for v in fams.values())
    control_pass = sum(v["correct_control"] for v in fams.values()) / n_ctl if n_ctl else None
    return {"score": score, "control_pass": control_pass, "n_control": n_ctl, "status": status, "reason": reason,
            "family_scores": {f: (fams[f]["accuracy"] if f in fams else None) for f in families},
            "families": fams}


def summary_line(target: str, split: str, v: dict) -> str:
    """CLI 一行摘要；unavailable 的 target 在行尾列出已有的 family 分數（partial）。"""
    cp = v["control_pass"]
    drift = [f for f, fv in v["families"].items() if fv.get("spec_mismatch")]
    line = (f"{target:52} {split:8} {v['status']:12} score={v['score'] if v['score'] is None else round(v['score'], 4)}"
            f"  control_pass={cp if cp is None else round(cp, 2)} (n_control={v['n_control']})")
    if v["status"] != "ok":
        have = []
        for f, sc in v.get("family_scores", {}).items():
            if sc is None:
                continue
            fv = v.get("families", {}).get(f, {})
            # 只跑了部分題目的 family 要標出來，不能和跑完的看起來一樣
            mark = f" (incomplete {fv.get('n', '?')}/{fv.get('expected_n', '?')})" if fv.get("complete") is False else ""
            have.append(f"{f}={sc:.2f}{mark}")
        if have:
            line += "  partial: " + ", ".join(have)
    if drift:
        line += f"  [spec_mismatch: {', '.join(drift)} — log 與當期題集不是同一版]"
    return line


def release_blockers(release: str, manifests: dict[tuple[str, str], dict], runs: dict[tuple[str, str, str], dict]) -> list[str]:
    """擋住 release 的項目（空 = 可以出 release）。family 狀態以 manifest 上的 hash 對 reviews.yaml 判定。"""
    out = []
    for (family, split), m in sorted(manifests.items()):
        st = status_for(FAMILIES / family, m)
        if st["status"] != "reviewed":
            version = "／".join(f"{k.removesuffix('_sha256')} {(m.get(k) or '—')[:12]}" for k in HASH_FIELDS)
            out.append(f"{family}/{split}：產生題目的版本（{version}）沒有對應的 approved 簽核"
                       f"（silver/families/{family}/reviews.yaml）")
        elif m.get("family_status") != "reviewed":
            cmd = (f"python3 gold/generate.py {family} --release {release} --split {split} "
                   f"--n {m['n'] + m.get('rejected', 0)} --as-of {m['as_of']}")
            if split == "private":
                cmd = "TAIWANEVAL_PRIVATE_SALT=… " + cmd
            out.append(f"{family}/{split}：已簽核，但 manifest 的 family_status 仍是 {m.get('family_status')}"
                       f"（題集在簽核前產生）；重生 gold：{cmd}")
    for (target, family, split), run in sorted(runs.items()):
        if run.get("spec_mismatch"):
            diff = "、".join(run["spec_mismatch"].get("differs", []))
            out.append(f"{target} {family}/{split}：log {run['log_file']} 與當期題集不是同一版"
                       f"{f'（{diff}）' if diff else ''}，release 前要重跑")
        elif not run.get("fake") and not target.startswith("fake-"):
            k = (run.get("repeats") or {}).get("n_runs", 1)
            if k < MIN_REPEATS:
                out.append(f"{target} {family}/{split}：同版完整 run 只有 {k} 次，release 需要至少 {MIN_REPEATS} 次"
                           f"（執行間變異）")
    # 模型 target 在它跑過的 split 上，每個有 manifest 的 family 都要有紀錄。沒有 log、或 log 還在寫（started，
    # collect_runs 不採用）的 family 不在 runs 裡，上面的檢查看不到；不擋的話它會以 unavailable 進 release。
    for target, split in sorted({(t, s) for t, _, s in runs}):
        if target.startswith("fake-") or any(r.get("fake") for (t, _, s), r in runs.items() if (t, s) == (target, split)):
            continue
        for family in sorted(f for f, s in manifests if s == split):
            if (target, family, split) not in runs:
                out.append(f"{target} {family}/{split}：沒有可採用的 log（沒跑過，或 log 還沒寫完），"
                           f"release 需要每個 family 至少 {MIN_REPEATS} 次同版完整 run")
    return out


def aggregate(release: str, mode: str = "preview", source: str = "auto") -> dict:
    if mode not in MODES:
        raise ValueError(f"mode 只能是 {'｜'.join(MODES)}，收到 {mode!r}")
    manifests = load_manifests(release)
    registry = dataset_registry.load(REGISTRY)
    dataset = registry.resolve_release(release, {f for f, _ in manifests})   # 混了兩個資料集 → RegistryError，不寫檔
    runs = collect_runs(release, manifests, source)   # 得分表與紀錄不一致 → ScoreTableError，不寫檔
    blockers = release_blockers(release, manifests, runs)
    if registry.dataset_of_release(release) is None:
        blockers.insert(0, f"release {release} 沒有登記在 datasets.yaml（{dataset.id} 的 releases）；"
                           f"新 release 的名稱格式是 {dataset.release_name_format}")
    if mode == "release" and blockers:
        raise ReleaseGateError(blockers)       # 不寫任何檔案
    families = sorted({k[0] for k in manifests})
    weights = {f: dataset.default_weights.get(f, 1.0) for f in families}

    # family 層：per (target, split)
    by_target: dict[str, dict] = defaultdict(lambda: {"splits": defaultdict(dict)})
    for (target, family, split), run in runs.items():
        mf = manifests.get((family, split))
        expected = mf["n"] if mf else None
        complete = expected is not None and run["n"] == expected
        by_branch = defaultdict(lambda: [0, 0, False])
        for it in run["items"]:
            by_branch[it["branch"]][0] += it["correct"]; by_branch[it["branch"]][1] += 1
            by_branch[it["branch"]][2] = it["control"]
        by_target[target]["splits"][split][family] = {
            "n": run["n"], "expected_n": expected, "correct": run["correct"],
            # accuracy 只算非 control 分支；control_pass 另報（見檔頭說明）
            "n_scored": run["n_scored"], "correct_scored": run["correct_scored"],
            # 有同版重複 run 時用平均（repeats 另附每次的值與範圍）；否則是代表那一次的 accuracy
            "accuracy": (run["repeats"]["mean"] if run.get("repeats", {}).get("n_runs")
                         else run["correct_scored"] / run["n_scored"] if run["n_scored"] else None),
            "repeats": run.get("repeats"),
            "n_control": run["n_control"], "correct_control": run["correct_control"],
            "control_pass": run["correct_control"] / run["n_control"] if run["n_control"] else None,
            "complete": complete, "tier": family_tier(family), "answer_mode": run["answer_mode"],
            "phrasing_consistency": phrasing_consistency(run["items"]),
            "spec_mismatch": run["spec_mismatch"],
            "n_error": run.get("n_error", 0),   # 逾時／取消等沒有分數的題，已算答錯
            "by_branch": {b: {"correct": c, "n": n, "accuracy": c / n, "control": ctl}
                          for b, (c, n, ctl) in sorted(by_branch.items())},
            "log_file": run["log_file"], "log_sha256": run["log_sha256"], "run_id": run["run_id"],
            "model": run["model"], "fake": run["fake"],
        }

    # aggregate 層：每 target 每 split 一個分數；任何 headline family 不完整 → 無分數
    targets_out = {}
    for target, d in by_target.items():
        splits_out = {}
        for split, fams in d["splits"].items():
            splits_out[split] = summarize_split(fams, families, weights)
        gap = None
        if "public" in splits_out and "private" in splits_out and all(splits_out[s]["score"] is not None for s in ("public", "private")):
            gap = splits_out["public"]["score"] - splits_out["private"]["score"]
        targets_out[target] = {"splits": splits_out, "public_private_gap": gap}

    pins = {}
    for (family, split), m in manifests.items():
        st = status_for(FAMILIES / family, m)
        r = st["review"]
        pins.setdefault(family, {})[split] = {
            "family_version": m["family_version"], "family_status": st["status"],
            "review": {k: r[k] for k in ("reviewer", "role", "date", "verdict")} if r else None,
            **{k: m.get(k) for k in HASH_FIELDS}, "generator_version": m["generator_version"],
            "n": m["n"], "rejected": m.get("rejected", 0), "as_of": m.get("as_of"),   # 重生題集要用同一個 as_of
            "salt": m["salt"], "lineage": m["lineage"],
        }

    index = {
        "index_name": dataset.name,
        "release": release,
        "methodology_version": METHODOLOGY_VERSION,
        "mode": mode, "release_blockers": blockers,
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "weights": weights, "headline_tiers": sorted(HEADLINE_TIERS),
        "families": families, "pins": pins,
        "targets": targets_out,
        "notes": [
            "evaluation_target = model@quantization@hardware；不同 target 不合併。",
            "分母固定為 manifest n；缺題不補 0、不重加權，aggregate 直接 unavailable。",
            "fake-* target 是 pipeline 驗證用的假 solver，不是模型。",
            "accuracy 只算非 control 分支（n_scored）；control 分支（對照題）另報 control_pass，不進 headline。",
            "answer_mode（direct／cot）記在每個 family 的 run 裡；target 命名慣例加 @direct／@cot 後綴，不同 answer_mode 不合併。",
            "spec_mismatch 非 null 表示該 run 的題目與當期題集不是同一版（family 版本、spec／rule／templates／sampler hash 或產生器版本不同，differs 列出哪幾個），分數照算但應重跑。",
            "mode=preview 的 index 不是正式 release：release_blockers 列出擋住 release 的項目；mode=release 時該清單必為空。",
            "pins 的 family_status 由 silver/families/<id>/reviews.yaml 推得：manifest 上的 spec／rule／templates／sampler hash 有對應的 approved 簽核才是 reviewed。",
            "unavailable 的 target 仍附 family_scores（partial 顯示）：已跑完的 family 各自的 accuracy；不給總分、不進排名。",
        ],
    }
    out_dir = INDEX / release
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    with (out_dir / "items.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["evaluation_target", "family", "split", "answer_mode", "item_id", "branch", "control", "gold", "answer", "correct", "log_sha256"])
        for (target, family, split), run in sorted(runs.items()):
            for it in run["items"]:
                w.writerow([target, family, split, run["answer_mode"], it["item_id"], it["branch"], int(it["control"]), it["gold"], it["answer"], int(it["correct"]), run["log_sha256"][:12]])
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--mode", choices=MODES, default="preview",
                    help="preview（預設）照寫 index 並列出 release 阻擋項；release 有任何阻擋項就失敗、不寫檔")
    ap.add_argument("--source", choices=SOURCES, default="auto",
                    help="auto（預設）：有 eval/logs/<release>/*.eval 就由紀錄算，並與逐題得分表比對；沒有紀錄就讀得分表。"
                         "logs／scores：只用指定的來源")
    a = ap.parse_args(argv)
    try:
        idx = aggregate(a.release, mode=a.mode, source=a.source)
    except (ReleaseGateError, dataset_registry.RegistryError, ScoreTableError) as e:
        print(str(e))
        return 1
    for t, d in idx["targets"].items():
        for s, v in d["splits"].items():
            print(summary_line(t, s, v))
    if idx["release_blockers"]:
        print(f"preview：{len(idx['release_blockers'])} 個 release 阻擋項（index.json 的 release_blockers）")
    print(f"→ index/{a.release}/index.json, items.csv（mode={idx['mode']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
