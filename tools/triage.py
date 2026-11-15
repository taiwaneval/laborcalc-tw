"""Triage：把 inventory 的每個 unit 分到五類，決定 coverage 的分母。

  computable     有輸入、有唯一數值／布林輸出，可寫成函數        → Tier 1 family
  lookup         答案明確但非計算（天數、金額上限、年齡、期限）  → Tier 2 錨定題
  definitional   定義、程序、義務、授權、罰則                    → 不出題或 Tier 2
  discretionary  含裁量、不確定法律概念                          → 不做
  delegated      「由中央主管機關定之」等，規則在子法或公告      → 追到子法再 triage

兩個階段：
  1. heuristic  規則式標記（關鍵字），產生 flags，不下結論
  2. llm        LLM 逐 unit 分類＋理由＋候選 family（由外部提供 llm_labels.json）
合併後寫出 triage.yaml；每筆帶 review: pending，人工複核後改為 confirmed / overridden。

用法：
  python3 tools/triage.py N0030001 --stage heuristic      # 產生 prompts/ 與 flags
  python3 tools/triage.py N0030001 --stage merge          # 合併 llm_labels.json → triage.yaml
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

from paths import inventory_dir

CATEGORIES = ["computable", "lookup", "definitional", "discretionary", "delegated"]

# 分類程序（FLAG_PATTERNS、PROMPT_HEADER、merge 規則）的版本，寫進 triage.yaml 與每題 lineage。
# 改動程序而改變輸出時 bump；不從上一版 triage.yaml 沿用，否則從零重建會得到別的版本。
TRIAGE_VERSION = "0.1.1"

# 規則式標記：只標不判
FLAG_PATTERNS = {
    # 「相當於」是等值用語（相當於一個月平均工資），不是裁量；用 negative lookahead 排除
    "discretion": r"必要時|合理|相當(?!於)|適當|酌|得視|情節|顯然|重大|正當理由|不得損及",
    "delegation": r"由中央主管機關(定|訂|核定|公告|指定)|另定之|另以法律定之|由.{0,6}主管機關.{0,4}定之|辦法.{0,6}定之",
    "numeric": r"[一二三四五六七八九十百千]+(日|小時|週|個月|年|歲|分之|倍|次|人|元)|百分之|加給|加倍|加成",
    "formula_words": r"計算|平均|按.{0,10}工資額|除以|乘以|比例|加給|發給|折算",
    "obligation": r"應(於|依|給|置備|保存|報|通知|公告|揭示|提供)|不得(拒絕|預告|終止|扣留|延長)",
    "penalty": r"罰鍰|處.{0,6}以下|刑|拘役|公布其名稱",
    "definition": r"定義如下|指受|指僱用|指.{0,20}者。|謂",
    "procedure": r"報請|核備|備查|核定|同意後|公告|書面",
    "deleted": r"^（刪除）$",
}


def heuristic_flags(text: str) -> list[str]:
    return [k for k, p in FLAG_PATTERNS.items() if re.search(p, text)]


PROMPT_HEADER = """你是法規可計算性分類員。對每一個 unit（法規的一項，含其各款）分到下列五類之一，並給一句理由；
若為 computable 或 lookup，另給一個候選 family id（snake_case，描述它能出什麼題）與這題的輸入／輸出。

類別定義：
- computable：有明確輸入、有唯一數值或布林輸出，可寫成純函數。例：延長工時加班費倍率、特休天數階梯、資遣預告期。
- lookup：答案是條文中一個明確的數字／期限／門檻，但不是計算。例：工資清冊保存五年、正常工時每日八小時。
- definitional：定義、程序、義務、授權、罰則，沒有可問的唯一答案，或答案是「是否應該」。
- discretionary：含裁量或不確定法律概念（必要時、合理、相當、適當），答案依個案。
- delegated：規則本體在子法、公告或主管機關核定（由中央主管機關定之），本條只是授權。

判準提醒：
- 一項同時含數值與裁量：若數值部分可獨立成題（如「每日不得超過二小時」）仍可 lookup/computable，理由寫明切割方式。
- 「以上」「以下」之倍率（加給三分之一以上）視為法定下限，可 computable。
- 罰則條文（罰鍰金額範圍）歸 definitional，不歸 lookup——範圍不是唯一答案。

輸出 JSON 陣列，每筆：
{"unit_id": "...", "category": "...", "reason": "...", "family": "snake_case 或 null", "io": "輸入→輸出 或 null"}

以下是 units：
"""


def write_prompts(pcode: str, units: list[dict], batch: int = 40) -> None:
    pdir = inventory_dir(pcode) / "prompts"
    pdir.mkdir(exist_ok=True)
    live = [u for u in units if not u["deleted"]]
    for i in range(0, len(live), batch):
        chunk = live[i:i + batch]
        body = "\n\n".join(f"[{u['unit_id']}]（{u['article']} 第 {u['para']} 項｜{u['chapter']}）\n{u['text']}" for u in chunk)
        (pdir / f"batch_{i // batch + 1:02d}.txt").write_text(PROMPT_HEADER + body)
    print(f"wrote {len(range(0, len(live), batch))} prompt batches to {pdir}")


def stage_heuristic(pcode: str) -> None:
    units = json.loads((inventory_dir(pcode) / "inventory.json").read_text())
    for u in units:
        u["flags"] = heuristic_flags(u["text"])
    (inventory_dir(pcode) / "inventory.json").write_text(json.dumps(units, ensure_ascii=False, indent=1))
    write_prompts(pcode, units)
    from collections import Counter
    c = Counter(f for u in units for f in u["flags"])
    print("flag counts:", dict(c))


def stage_merge(pcode: str) -> None:
    units = json.loads((inventory_dir(pcode) / "inventory.json").read_text())
    labels_path = inventory_dir(pcode) / "llm_labels.json"
    labels = {x["unit_id"]: x for x in json.loads(labels_path.read_text())}
    # 既有的人工複核決定要保留：同 unit_id 且 content_hash 未變才沿用；條文變了則回到 pending 並留下 stale 註記
    prev_path = inventory_dir(pcode) / "triage.yaml"
    prev = {r["unit_id"]: r for r in yaml.safe_load(prev_path.read_text())["units"]} if prev_path.exists() else {}
    rows = []
    missing = []
    carried = stale = 0
    for u in units:
        if u["deleted"]:
            rows.append({"unit_id": u["unit_id"], "category": "deleted", "review": "n/a"})
            continue
        lab = labels.get(u["unit_id"])
        if not lab:
            missing.append(u["unit_id"]); continue
        assert lab["category"] in CATEGORIES, (u["unit_id"], lab["category"])
        # 規則式標記與 LLM 分類衝突時標出來，給人工複核優先看
        conflicts = []
        if lab["category"] in ("computable", "lookup") and "discretion" in u["flags"]:
            conflicts.append("heuristic:discretion")
        if lab["category"] in ("computable", "lookup") and "delegation" in u["flags"]:
            conflicts.append("heuristic:delegation")
        if lab["category"] == "definitional" and "formula_words" in u["flags"] and "numeric" in u["flags"]:
            conflicts.append("heuristic:numeric+formula")
        row = {
            "unit_id": u["unit_id"], "article": u["article"], "para": u["para"], "chapter": u["chapter"],
            "category": lab["category"], "reason": lab.get("reason", ""),
            "family": lab.get("family"), "io": lab.get("io"),
            "flags": u["flags"], "conflicts": conflicts, "refs": u["refs"],
            "content_hash": u["content_hash"][:12],
            "review": "pending", "reviewer": None, "reviewed_at": None, "override": None, "review_note": None,
        }
        p = prev.get(u["unit_id"])
        if p and p.get("review") not in (None, "pending", "n/a"):
            if p.get("content_hash") == row["content_hash"]:
                # 沿用人工決定（含覆寫後的 category / family / io）
                for k in ("category", "family", "io", "review", "reviewer", "reviewed_at", "override", "review_note"):
                    row[k] = p.get(k)
                carried += 1
            else:
                row["review_note"] = f"STALE: 條文已變更（原 hash {p.get('content_hash')}），先前複核 {p.get('review')} by {p.get('reviewer')} 作廢"
                stale += 1
        rows.append(row)
    if missing:
        print("MISSING labels:", missing)
    out = {
        "pcode": pcode,
        "triage_version": TRIAGE_VERSION,
        "labeler": "llm-first-pass (Claude); human review via reviews/*.yaml",
        "units": rows,
    }
    print(f"carried over {carried} reviewed units, {stale} marked STALE")
    (inventory_dir(pcode) / "triage.yaml").write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=200))
    from collections import Counter
    c = Counter(r["category"] for r in rows)
    print("categories:", dict(c))
    print("conflicts:", sum(bool(r.get("conflicts")) for r in rows))
    print(f"→ {inventory_dir(pcode) / 'triage.yaml'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcode")
    ap.add_argument("--stage", choices=["heuristic", "merge"], required=True)
    a = ap.parse_args(argv)
    (stage_heuristic if a.stage == "heuristic" else stage_merge)(a.pcode)


if __name__ == "__main__":
    main()
