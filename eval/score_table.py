"""逐題得分表：每個「評測對象 × 題目」一列，只有取出的整數答案、對錯與錯答歸因，沒有模型作答原文。

原始評測紀錄（Inspect 的 .eval）含模型原文，不隨公開版散布。這張表是它們的公開替代品：
eval/aggregate.py 只讀這張表就能算出同一份 index，所以沒有原始紀錄的 repo 也能重現排行榜。

位置：eval/scores/<release>/
  runs.json    每份被採用的紀錄一筆：檔名、sha256、Inspect 狀態、run_id、建立時間、模型、evaluation_target、
               family、split、answer_mode、thinking、fake、versions（紀錄裡各題帶的題集版本欄位）、n_items
  items.jsonl  每列一題（欄位見 ITEM_FIELDS），依 runs.json 的順序、同一份紀錄內依題目第一次出現的順序。
               log_sha256 指回 runs.json 的那一筆

收哪些紀錄：eval/logs/<release>/ 裡 Inspect 狀態是 success／error／cancelled 的每一份（與 aggregate 相同，
含 fake-* 假 solver 與題集改版前的舊紀錄：aggregate 需要它們才能算出同一份 index）；還在寫入的（started）不收。
同一題因重試留下多筆時只留一列：有分數的那次優先，否則最後一次。

每一列：
  parsed_answer  評分器從作答取出的整數；取不到整數或沒有分數時為 null
  correct        評分器的判定；沒有分數的題算答錯
  error          沒有分數的原因（timeout／cancelled／error／unscored），有分數時為 null
  attribution    correct｜unscored｜錯答命中的第一個假說（silver/families/<id>/hypotheses.py）｜no_integer｜unexplained

用法：
  python3 eval/score_table.py --release 2026Q4            從 eval/logs/<release>/ 產生（需要原始紀錄）
  python3 eval/score_table.py --release 2026Q4 --check    不寫檔，檢查已提交的表；不一致即結束碼 1

產生時的檢查：表裡每份紀錄的題數與答對題數，必須等於 aggregate 直接由紀錄算出的（兩邊各自實作去重與計數）；
不一致就失敗、不寫檔。取出的答案與正解必須是整數的標準寫法，否則也失敗。
--check：有原始紀錄時，由紀錄重新產生的內容要與已提交的逐 byte 相同；index/<release>/index.json 採用的每個 run
都要在表裡，它的題數、答對題數與紀錄的 sha256 要與 index 相同。沒有原始紀錄時只做後一項。

新增或更換紀錄後的順序：score_table.py → aggregate.py → tools/site_data.py。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = "0.1.0"
RUNS_FILE, ITEMS_FILE = "runs.json", "items.jsonl"
# 紀錄裡每題帶的題集版本：family 版本、四個簽核 hash 與產生器版本（aggregate 用來判斷紀錄與當期題集是不是同一版）
VERSION_FIELDS = ("family_version", "spec_sha256", "rule_sha256", "templates_sha256", "sampler_sha256", "generator_version")
USABLE_LOG_STATUSES = ("success", "error", "cancelled")
RUN_FIELDS = ("log_file", "log_sha256", "log_status", "run_id", "created", "model", "evaluation_target", "family", "split",
              "answer_mode", "thinking", "fake", "versions")
ITEM_FIELDS = ("evaluation_target", "family", "split", "item_id", "branch", "control", "pair_id", "gold",
               "parsed_answer", "correct", "error", "attribution", "log_sha256")
COUNT_FIELDS = ("n", "correct", "n_scored", "correct_scored", "n_control", "correct_control")


class ScoreTableError(Exception):
    """得分表缺漏、格式不對，或與評測紀錄／index 不一致。"""


# --------------------------------------------------------------------------- 從一份紀錄取出每題一列
def kept_samples(samples) -> list:
    """同一題因重試留下多筆 sample 時只留一筆：有分數的那次優先，否則最後一次。順序照題目第一次出現的位置。"""
    kept: dict[str, object] = {}
    for s in samples:
        k = kept.get(s.id)
        if k is not None and (k.scores or {}).get("integer_match") is not None:
            continue
        kept[s.id] = s
    return list(kept.values())


def error_kind(err) -> str | None:
    if err is None:
        return None
    msg = str(getattr(err, "message", err))
    return "timeout" if "Timeout" in msg else "cancelled" if "Cancelled" in msg else "error"


def _attribute_errors():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import attribute_errors
    return attribute_errors


class FamilyRules:
    """一個 family 的對照分支與歸因設定（歸因規則是 eval/attribute_errors.py 的 classify_wrong）。"""

    def __init__(self, family: str, families_dir: Path):
        spec = yaml.safe_load((families_dir / family / "spec.yaml").read_text(encoding="utf-8"))
        self.controls = {b["id"] for b in spec.get("branches", []) if b.get("control")}
        s = importlib.util.spec_from_file_location(f"{family}_hypotheses_score_table", families_dir / family / "hypotheses.py")
        self.hyp = importlib.util.module_from_spec(s)
        s.loader.exec_module(self.hyp)
        self.tol = getattr(self.hyp, "ATTRIBUTION_TOL", 1)
        self.exact = set(getattr(self.hyp, "EXACT_MATCH", ()))
        self.rounding = getattr(self.hyp, "ROUNDING_FALLBACK", 0)

    def attribution(self, params: dict, gold: int, ans: int | None, correct: bool, error: str | None) -> str:
        if error:
            return "unscored"
        if correct:
            return "correct"
        hits = _attribute_errors().classify_wrong(self.hyp, params, gold, ans, self.tol, self.exact, self.rounding)
        return hits[0] if hits else "unexplained"


def _integer(value, where: str) -> int:
    """value 必須是整數的標準寫法（"1900"、"-3"、"0"）。別的字串可能帶著作答原文，讀回來也還原不出同一個值。"""
    text = str(value)
    try:
        number = int(text)
    except ValueError:
        number = None
    if number is None or str(number) != text:
        raise ScoreTableError(f"{where} 不是整數的標準寫法（長度 {len(text)} 的字串）；得分表只收整數")
    return number


def sample_row(s, rules: FamilyRules) -> dict:
    """一筆 sample → 與評測對象無關的欄位（branch … attribution）。"""
    sc = (s.scores or {}).get("integer_match")
    gold = _integer(s.target if isinstance(s.target, str) else s.target[0], f"{s.id} 的正解")
    ans = _integer(sc.answer, f"{s.id} 取出的答案") if sc is not None and sc.answer is not None else None
    correct = bool(sc is not None and sc.value == "C")
    err = None if sc is not None else (error_kind(s.error) or "unscored")
    branch = s.metadata.get("branch")
    return {"item_id": s.id, "branch": branch, "control": branch in rules.controls, "pair_id": s.metadata.get("pair_id"),
            "gold": gold, "parsed_answer": ans, "correct": correct, "error": err,
            "attribution": rules.attribution(s.metadata.get("params") or {}, gold, ans, correct, err)}


def version_key(t) -> tuple:
    """版本欄位的排序鍵：舊紀錄可能缺欄位（None），比的時候當空字串。"""
    return tuple("" if x is None else str(x) for x in t)


def run_from_log(path: Path, families_dir: Path, rules: dict[str, FamilyRules] | None = None) -> dict | None:
    """一份紀錄 → {RUN_FIELDS…, "items": [ITEM_FIELDS…]}；還在寫入（或其他不採用的狀態）回 None。"""
    from inspect_ai.log import read_eval_log
    log = read_eval_log(str(path))
    if log.status not in USABLE_LOG_STATUSES:
        return None
    md = log.eval.metadata or {}
    family = md["family"]
    rules = {} if rules is None else rules
    fr = rules.get(family) or rules.setdefault(family, FamilyRules(family, families_dir))
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    target = md.get("evaluation_target", "unspecified")
    seen = sorted({tuple(s.metadata.get(k) for k in VERSION_FIELDS) for s in log.samples}, key=version_key)
    items = [{"evaluation_target": target, "family": family, "split": md["split"], **sample_row(s, fr), "log_sha256": sha}
             for s in kept_samples(log.samples)]
    return {"log_file": Path(path).name, "log_sha256": sha, "log_status": log.status, "run_id": log.eval.run_id,
            "created": log.eval.created, "model": log.eval.model, "evaluation_target": target, "family": family,
            "split": md["split"], "answer_mode": md.get("answer_mode") or "direct", "thinking": md.get("thinking"),
            "fake": md.get("fake"), "versions": [dict(zip(VERSION_FIELDS, t)) for t in seen], "items": items}


def from_logs(logs_dir: Path, families_dir: Path) -> list[dict]:
    """logs_dir（eval/logs/<release>/）裡每份可採用的紀錄，依檔名排序。"""
    rules: dict[str, FamilyRules] = {}
    runs = [run_from_log(f, families_dir, rules) for f in sorted(Path(logs_dir).glob("*.eval"))]
    return [r for r in runs if r is not None]


# --------------------------------------------------------------------------- 寫出與讀回
def render(runs: list[dict], release: str) -> dict[str, bytes]:
    """{檔名: bytes}。同樣的輸入產生同樣的 bytes。"""
    shas = [r["log_sha256"] for r in runs]
    if len(set(shas)) != len(shas):
        raise ScoreTableError("有兩份紀錄的 sha256 相同（同一個檔被複製了？）；每列靠 log_sha256 指回它的紀錄")
    doc = {"schema_version": SCHEMA_VERSION, "release": release, "item_fields": list(ITEM_FIELDS),
           "runs": [{**{k: r[k] for k in RUN_FIELDS}, "n_items": len(r["items"])} for r in runs]}
    lines = [json.dumps({k: it[k] for k in ITEM_FIELDS}, ensure_ascii=False) for r in runs for it in r["items"]]
    return {RUNS_FILE: (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8"),
            ITEMS_FILE: "".join(ln + "\n" for ln in lines).encode("utf-8")}


def exists(table_dir: Path) -> bool:
    return (Path(table_dir) / RUNS_FILE).exists()


def read(table_dir: Path) -> list[dict]:
    """讀回 render 寫出的表：[{RUN_FIELDS…, "items": […]}]，順序與檔案相同。格式不對就丟 ScoreTableError。"""
    table_dir = Path(table_dir)
    where = f"eval/scores/{table_dir.name}"
    if not exists(table_dir):
        raise ScoreTableError(f"沒有得分表：{where}/{RUNS_FILE} 不存在")
    try:
        doc = json.loads((table_dir / RUNS_FILE).read_text(encoding="utf-8"))
        rows = [json.loads(ln) for ln in (table_dir / ITEMS_FILE).read_text(encoding="utf-8").splitlines() if ln]
    except (OSError, ValueError) as e:
        raise ScoreTableError(f"{where} 讀不出來：{type(e).__name__}: {e}") from e
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ScoreTableError(f"{where}/{RUNS_FILE} 的 schema_version 是 {doc.get('schema_version')!r}，本工具讀的是 {SCHEMA_VERSION}")
    runs = [{**{k: r.get(k) for k in RUN_FIELDS}, "items": []} for r in doc["runs"]]
    by_sha = {r["log_sha256"]: r for r in runs}
    if len(by_sha) != len(runs):
        raise ScoreTableError(f"{where}/{RUNS_FILE} 有重複的 log_sha256")
    for no, row in enumerate(rows, 1):
        if set(row) != set(ITEM_FIELDS):
            raise ScoreTableError(f"{where}/{ITEMS_FILE} 第 {no} 列的欄位不是 ITEM_FIELDS")
        run = by_sha.get(row["log_sha256"])
        if run is None:
            raise ScoreTableError(f"{where}/{ITEMS_FILE} 第 {no} 列的 log_sha256 不在 {RUNS_FILE} 裡")
        if (row["evaluation_target"], row["family"], row["split"]) != (run["evaluation_target"], run["family"], run["split"]):
            raise ScoreTableError(f"{where}/{ITEMS_FILE} 第 {no} 列的 evaluation_target／family／split 與它的紀錄 {run['log_file']} 不同")
        run["items"].append(row)
    for r, src in zip(runs, doc["runs"]):
        if len(r["items"]) != src.get("n_items"):
            raise ScoreTableError(f"{where}：{r['log_file']} 在 {RUNS_FILE} 記 {src.get('n_items')} 題，{ITEMS_FILE} 有 {len(r['items'])} 列")
    return runs


# --------------------------------------------------------------------------- 給 aggregate 的形式，與兩個來源的比對
def as_raw(run: dict) -> dict:
    """得分表的一份紀錄 → aggregate 計分用的形式（與 aggregate.log_runs 由紀錄讀出的相同；答案與正解是字串）。"""
    return {
        **{k: run[k] for k in ("log_file", "log_sha256", "log_status", "run_id", "created", "model", "fake", "answer_mode",
                               "evaluation_target", "family", "split")},
        "versions": sorted((tuple(v.get(k) for k in VERSION_FIELDS) for v in run["versions"]), key=version_key),
        "items": [{"item_id": it["item_id"], "branch": it["branch"], "control": it["control"], "correct": it["correct"],
                   "answer": None if it["parsed_answer"] is None else str(it["parsed_answer"]), "gold": str(it["gold"]),
                   "pair_id": it["pair_id"], "error": it["error"]} for it in run["items"]],
    }


def counts(items: list[dict]) -> dict[str, int]:
    scored = [i for i in items if not i["control"]]
    control = [i for i in items if i["control"]]
    return {"n": len(items), "correct": sum(i["correct"] for i in items),
            "n_scored": len(scored), "correct_scored": sum(i["correct"] for i in scored),
            "n_control": len(control), "correct_control": sum(i["correct"] for i in control)}


def difference(table: list[dict], logs: list[dict]) -> str | None:
    """得分表與評測紀錄（兩邊都是 as_raw 的形式）第一個不同之處；相同回 None。"""
    t, g = {r["log_file"]: r for r in table}, {r["log_file"]: r for r in logs}
    only_logs, only_table = sorted(set(g) - set(t)), sorted(set(t) - set(g))
    if only_logs:
        return f"紀錄 {'、'.join(only_logs)} 不在得分表裡"
    if only_table:
        return f"得分表的 {'、'.join(only_table)} 沒有對應的紀錄"
    for name in sorted(t):
        a, b = t[name], g[name]
        for k in a:
            if k != "items" and a[k] != b[k]:
                return f"{name}：{k} 得分表記 {a[k]!r}，紀錄是 {b[k]!r}"
        ca, cb = counts(a["items"]), counts(b["items"])
        for k in COUNT_FIELDS:
            if ca[k] != cb[k]:
                return f"{name}：{k}（題數／答對題數）得分表 {ca[k]}，由紀錄算出 {cb[k]}"
        for x, y in zip(a["items"], b["items"]):
            if x != y:
                diff = [k for k in x if x[k] != y.get(k)]
                return f"{name}：{x['item_id']} 的 {'、'.join(diff)} 與紀錄不同"
    return None


def index_problems(table: list[dict], ix: dict) -> list[str]:
    """index.json 採用的每個 run 都要在表裡；代表 run 的題數、答對題數與紀錄 sha256 要相同。"""
    by_file = {r["log_file"]: r for r in table}
    problems = []
    for target, t in sorted(ix.get("targets", {}).items()):
        for split, s in sorted(t["splits"].items()):
            for family, r in sorted((s.get("families") or {}).items()):
                who = f"{target} {family}/{split}"
                files = (r.get("repeats") or {}).get("log_files") or [r["log_file"]]
                missing = [f for f in dict.fromkeys([*files, r["log_file"]]) if f not in by_file]
                if missing:
                    problems.append(f"{who}：index 採用的 {'、'.join(missing)} 不在得分表裡")
                    continue
                run = by_file[r["log_file"]]
                if (run["evaluation_target"], run["family"], run["split"]) != (target, family, split):
                    problems.append(f"{who}：得分表把 {r['log_file']} 記在 {run['evaluation_target']} {run['family']}/{run['split']}")
                if run["log_sha256"] != r["log_sha256"]:
                    problems.append(f"{who}：{r['log_file']} 的 log_sha256 得分表與 index 不同")
                got = counts(run["items"])
                problems += [f"{who}：{k} 得分表 {got[k]}、index {r.get(k)}" for k in COUNT_FIELDS if got[k] != r.get(k)]
    return problems


# --------------------------------------------------------------------------- 產生與檢查（路徑用 aggregate 的）
def _aggregate():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import aggregate
    return aggregate


def build(release: str) -> list[Path]:
    """由 eval/logs/<release>/ 產生 eval/scores/<release>/。先核對題數與答對題數等於 aggregate 由紀錄算出的，才寫檔。"""
    agg = _aggregate()
    runs = from_logs(agg.LOGS / release, agg.FAMILIES)
    if not runs:
        raise ScoreTableError(f"eval/logs/{release}/ 沒有可採用的評測紀錄；得分表只能從原始紀錄產生")
    d = difference([as_raw(r) for r in runs], agg.log_runs(release))
    if d:
        raise ScoreTableError(f"得分表與 aggregate 由紀錄算出的不一致（題數／答對題數要完全相同），未寫入任何檔案：{d}")
    blobs = render(runs, release)
    out = agg.SCORES / release
    out.mkdir(parents=True, exist_ok=True)
    for name, blob in blobs.items():
        (out / name).write_bytes(blob)
    return [out / name for name in blobs]


def check(release: str) -> list[str]:
    """已提交的得分表有沒有問題（空 list = 沒有）。見檔頭 --check。"""
    agg = _aggregate()
    table_dir, where = agg.SCORES / release, f"eval/scores/{release}"
    if not exists(table_dir):
        return [f"{where}/ 沒有得分表（python3 eval/score_table.py --release {release}）"]
    try:
        table = read(table_dir)
    except ScoreTableError as e:
        return [str(e)]
    problems = []
    if any((agg.LOGS / release).glob("*.eval")):
        try:
            fresh = render(from_logs(agg.LOGS / release, agg.FAMILIES), release)
        except ScoreTableError as e:
            return [f"無法由評測紀錄重新產生：{e}"]
        problems += [f"{where}/{name}：與由評測紀錄重新產生的內容不同（重跑 python3 eval/score_table.py --release {release}）"
                     for name, blob in fresh.items() if (table_dir / name).read_bytes() != blob]
    ij = agg.INDEX / release / "index.json"
    if ij.exists():
        problems += index_problems(table, json.loads(ij.read_text(encoding="utf-8")))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐題得分表：從評測紀錄產生，或檢查已提交的表")
    ap.add_argument("--release", required=True)
    ap.add_argument("--check", action="store_true", help="不寫檔；檢查已提交的表與評測紀錄（有的話）、index 是否一致")
    a = ap.parse_args(argv)
    if a.check:
        problems = check(a.release)
        for p in problems:
            print(f"✗ {p}")
        if not problems:
            print(f"✓ eval/scores/{a.release} 與評測紀錄（有的話）、index 一致")
        return 1 if problems else 0
    try:
        paths = build(a.release)
    except ScoreTableError as e:
        print(f"✗ {e}")
        return 1
    table = read(paths[0].parent)
    print(f"→ eval/scores/{a.release}/{RUNS_FILE}, {ITEMS_FILE}（{len(table)} 份紀錄、{sum(len(r['items']) for r in table)} 列）")
    return 0


if __name__ == "__main__":
    # 以腳本執行時本檔是 __main__；aggregate 會再匯入一次 score_table。交給那一份執行，例外類別才是同一個。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import score_table
    sys.exit(score_table.main())
