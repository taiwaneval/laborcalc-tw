"""錯答歸因：把一個 Inspect log 裡每個錯答，對上「錯用了哪條規則」的候選公式。

輸入：eval/logs/<release>/<log>.eval（rules_registry task，任何有 hypotheses.py 的 family）
輸出：stdout 摘要（依假說、依 branch），--json 另存逐題歸因。

方法：每題從 metadata.params 取參數，交給該 family 的 silver/families/<id>/hypotheses.py 算出一組「錯規則」假說值，
模型答案與哪個假說相差 ≤ tol（預設取 family 的 ATTRIBUTION_TOL：加班費 1 元、特休 0 日）就歸那一類；多個命中時取假說表順序最前者；
都不命中且該 family 設了 ROUNDING_FALLBACK、與現行規則的正解相差在其內 → rounding；否則 unexplained。
答案與現行規則的正解完全相同、卻被這份 log 判錯 → correct_under_current_rule：log 是用舊版 gold 評分的
（例：加班費 0.2.0 修正休息日第 9 小時起的倍率）。只看完全相同，不套 tol，免得把取整錯誤也算進來。
family 的 EXACT_MATCH 裡的假說（加班費的取整錯誤）同樣只認完全相同。
對照題（control 分支）另列不混進去。

假說不是法律判斷，是評測用的錯誤模型；命中只代表「數字上一致」。
用法：python3 eval/attribute_errors.py eval/logs/2026Q4/<log>.eval [--tol N] [--json out.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from inspect_ai.log import read_eval_log

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ROOT / "silver" / "families"


def load_family_module(family: str, name: str):
    """以唯一模組名載入 silver/families/<family>/<name>.py。"""
    s = importlib.util.spec_from_file_location(f"{family}_{name}", FAMILIES / family / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def attribution_tol(family: str) -> int:
    """該 family 的預設歸因容許差（hypotheses.py 的 ATTRIBUTION_TOL；未設為 1）。"""
    return getattr(load_family_module(family, "hypotheses"), "ATTRIBUTION_TOL", 1)


def classify_wrong(hyp_mod, params: dict, gold: int, ans: int | None, tol: int,
                   exact_only: set = frozenset(), rounding_fb: int = 0) -> list[str]:
    """一題錯答命中的假說（依假說表順序；空 = unexplained）。規則見檔頭。"""
    if ans is None:
        return ["no_integer"]
    hyp = hyp_mod.hypotheses(params)
    if ans == hyp["correct"] and gold != hyp["correct"]:
        return ["correct_under_current_rule"]
    hits = [k for k, v in hyp.items() if k != "correct" and abs(v - ans) <= (0 if k in exact_only else tol)]
    # 跟現行正解比，不跟 log 的 gold：用現行 gold 評分的 log 兩者相同；舊 log 跟現行正解比才有意義
    if not hits and rounding_fb and abs(ans - hyp["correct"]) <= rounding_fb:
        hits = [f"rounding(|Δ|≤{rounding_fb})"]
    return hits


def attribute(log_path: Path, tol: int | None = None) -> dict:
    log = read_eval_log(str(log_path))
    md = log.eval.metadata or {}
    family = md.get("family")
    if not (FAMILIES / str(family) / "hypotheses.py").exists():
        raise SystemExit(f"family={family!r} 沒有 hypotheses.py，無法歸因")
    hyp_mod = load_family_module(family, "hypotheses")
    rounding_fb = getattr(hyp_mod, "ROUNDING_FALLBACK", 0)
    exact_only = set(getattr(hyp_mod, "EXACT_MATCH", ()))
    if tol is None:
        tol = getattr(hyp_mod, "ATTRIBUTION_TOL", 1)
    spec = yaml.safe_load((FAMILIES / family / "spec.yaml").read_text())
    controls = {b["id"] for b in spec["branches"] if b.get("control")}

    rows = []
    for s in log.samples:
        sc = s.scores["integer_match"]
        p = s.metadata["params"]
        branch = s.metadata.get("branch")
        gold = int(s.target if isinstance(s.target, str) else s.target[0])
        ans = int(sc.answer) if sc.answer is not None else None
        row = {"item_id": s.id, "branch": branch, "control": branch in controls, "params": p,
               "gold": gold, "answer": ans, "correct": sc.value == "C", "hypotheses": []}
        if not row["correct"]:
            row["hypotheses"] = classify_wrong(hyp_mod, p, gold, ans, tol, exact_only, rounding_fb)
            row["primary"] = row["hypotheses"][0] if row["hypotheses"] else "unexplained"
            row["ratio_to_gold"] = round(ans / gold, 3) if ans is not None and gold else None
        rows.append(row)

    wrong = [r for r in rows if not r["correct"] and not r["control"]]
    wrong_ctl = [r for r in rows if not r["correct"] and r["control"]]
    by_hyp = Counter(r["primary"] for r in wrong)
    by_branch: dict[str, Counter] = defaultdict(Counter)
    for r in wrong:
        by_branch[r["branch"]][r["primary"]] += 1
    n_scored = sum(1 for r in rows if not r["control"])
    summary = {
        "log": log_path.name, "family": family, "evaluation_target": md.get("evaluation_target"), "answer_mode": md.get("answer_mode", "direct"),
        "model": log.eval.model, "tol": tol,
        "n": len(rows), "n_scored": n_scored, "n_control": len(rows) - n_scored,
        "wrong_scored": len(wrong), "wrong_control": len(wrong_ctl),
        "by_hypothesis": dict(by_hyp.most_common()),
        "by_branch": {b: dict(c.most_common()) for b, c in sorted(by_branch.items())},
        "unexplained_examples": [
            {"item_id": r["item_id"], "branch": r["branch"], "params": r["params"], "gold": r["gold"],
             "answer": r["answer"], "ratio_to_gold": r["ratio_to_gold"]}
            for r in wrong if r["primary"] == "unexplained"
        ][:12],
    }
    return {"summary": summary, "items": rows}


def print_summary(sm: dict) -> None:
    print(f"log: {sm['log']}\ntarget: {sm['evaluation_target']} · answer_mode={sm['answer_mode']} · model={sm['model']}")
    print(f"n={sm['n']} (實算 {sm['n_scored']} · 對照 {sm['n_control']})  錯答：實算 {sm['wrong_scored']} · 對照 {sm['wrong_control']}  tol=±{sm['tol']}")
    print("\n依假說（實算題錯答）：")
    for k, v in sm["by_hypothesis"].items():
        print(f"  {v:4d}  {100 * v / max(sm['wrong_scored'], 1):5.1f}%  {k}")
    print("\n依 branch：")
    for b, c in sm["by_branch"].items():
        top = " · ".join(f"{k} {v}" for k, v in list(c.items())[:4])
        print(f"  {b:3} 錯 {sum(c.values()):3d}  {top}")
    if sm["unexplained_examples"]:
        print("\nunexplained 範例（answer / gold / ratio）：")
        for e in sm["unexplained_examples"]:
            params = " ".join(f"{k}={v}" for k, v in e["params"].items())
            print(f"  {e['branch']:3} {params}  ans={e['answer']:>7}  gold={e['gold']:>6}  ×{e['ratio_to_gold']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--tol", type=int, default=None, help="歸因容許差；預設用該 family hypotheses.py 的 ATTRIBUTION_TOL")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    out = attribute(Path(a.log), a.tol)
    print_summary(out["summary"])
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"\n→ {a.json}")
