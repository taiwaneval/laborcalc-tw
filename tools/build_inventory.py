"""把 source.json 切成「條 → 項（含所屬各款）」的 inventory。

每個 unit 一列：
  unit_id        N0030001/24/2      （法規／條／項）
  article        第 24 條
  article_no     24                  （排序用；84-1 → 84.1）
  para           2
  chapter        第 四 章 工作時間、休息、休假
  text           該項全文（含款）
  content_hash   sha256(text)        （更新偵測用）
  refs           本項引用的其他條項（第三十二條第四項 → 32/4）
  flags          規則式標記（見 triage.py）

用法：python3 tools/build_inventory.py N0030001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from paths import bronze_dir, inventory_dir

ITEM_RE = re.compile(r"^[一二三四五六七八九十]+、")  # 款
ART_RE = re.compile(r"第 (\d+(?:-\d+)?) 條")
CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}


def cn_to_int(s: str) -> int:
    """中文數字 → 整數（支援到 999）。"""
    s = s.replace("零", "")
    if not s:
        return 0
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100; cur = 0
        elif ch == "十":
            total += (cur or 1) * 10; cur = 0
        else:
            cur = CN_NUM[ch]
    return total + cur


REF_RE = re.compile(r"第([一二三四五六七八九十百]+)條(?:之([一二三四五六七八九十]+))?(?:第([一二三四五六七八九十]+)項)?")


def extract_refs(text: str) -> list[str]:
    refs = []
    for m in REF_RE.finditer(text):
        art = str(cn_to_int(m.group(1)))
        if m.group(2):
            art += "-" + str(cn_to_int(m.group(2)))
        para = cn_to_int(m.group(3)) if m.group(3) else None
        refs.append(f"{art}/{para}" if para else art)
    return sorted(set(refs))


def split_paragraphs(content: str) -> list[str]:
    lines = [ln.rstrip() for ln in content.replace("\r\n", "\n").split("\n") if ln.strip()]
    paras: list[list[str]] = []
    for ln in lines:
        if ITEM_RE.match(ln) and paras:
            paras[-1].append(ln)  # 款附屬於前一項
        else:
            paras.append([ln])
    return ["\n".join(p) for p in paras]


def build(pcode: str) -> list[dict]:
    law = json.loads((bronze_dir(pcode) / "source.json").read_text())
    units: list[dict] = []
    chapter = ""
    for entry in law["法規內容"]:
        if "編章節" in entry:
            chapter = entry["編章節"].strip()
            continue
        art_label = entry["條號"].strip()
        m = ART_RE.search(art_label)
        art_no = m.group(1) if m else art_label
        content = entry["條文內容"]
        if content.strip() in ("（刪除）", "(刪除)"):
            units.append({
                "unit_id": f"{pcode}/{art_no}/1", "article": art_label, "article_no": art_no, "para": 1,
                "chapter": chapter, "text": "（刪除）", "deleted": True,
                "content_hash": hashlib.sha256("（刪除）".encode()).hexdigest(), "refs": [],
            })
            continue
        for i, text in enumerate(split_paragraphs(content), 1):
            units.append({
                "unit_id": f"{pcode}/{art_no}/{i}", "article": art_label, "article_no": art_no, "para": i,
                "chapter": chapter, "text": text, "deleted": False,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "refs": extract_refs(text),
            })
    return units


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcode")
    a = ap.parse_args(argv)
    units = build(a.pcode)
    out = inventory_dir(a.pcode) / "inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(units, ensure_ascii=False, indent=1))
    n_art = len({u["article"] for u in units})
    n_del = sum(u["deleted"] for u in units)
    print(f"{a.pcode}: {n_art} articles ({n_del} deleted), {len(units)} units → {out}")


if __name__ == "__main__":
    main()
