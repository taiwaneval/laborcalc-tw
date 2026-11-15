"""把一批人工複核決定寫回 triage.yaml。

reviews.yaml 格式：
  reviewer: Calvin Ku
  date: "2026-10-08"
  decisions:
    - { unit_id: N0030001/38/4, verdict: overridden, category: delegated, family: null, note: ... }
    - { unit_id: N0030001/12/1, verdict: confirmed, note: ... }

verdict=confirmed：保留 LLM 分類，記錄複核者與備註
verdict=overridden：改 category／family，原值存進 override.from。
  已覆寫過的 unit 再覆寫（例如訂正備註）時保留最初的 override.from，那是 LLM 原始分類，不能被覆寫後的值蓋掉。
用法：python3 tools/apply_review.py N0030001 silver/inventory/N0030001/reviews/<file>.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from paths import inventory_dir


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcode")
    ap.add_argument("reviews", type=Path)
    a = ap.parse_args(argv)

    tpath = inventory_dir(a.pcode) / "triage.yaml"
    triage = yaml.safe_load(tpath.read_text())
    reviews = yaml.safe_load(a.reviews.read_text())
    by_id = {r["unit_id"]: r for r in triage["units"]}

    for d in reviews["decisions"]:
        r = by_id[d["unit_id"]]
        r["review"] = d["verdict"]
        r["reviewer"] = reviews["reviewer"]
        r["reviewed_at"] = reviews["date"]
        r["review_note"] = d.get("note")
        if d["verdict"] == "overridden":
            if not (r.get("override") or {}).get("from"):
                r["override"] = {"from": {"category": r["category"], "family": r.get("family")}}
            if "category" in d:
                r["category"] = d["category"]
            if "family" in d:
                r["family"] = d["family"]
            if "io" in d:
                r["io"] = d["io"]
        print(f"{d['unit_id']:18} {d['verdict']:10} → {r['category']:13} {r.get('family') or ''}")

    tpath.write_text(yaml.safe_dump(triage, allow_unicode=True, sort_keys=False, width=200))
    n = sum(1 for r in triage["units"] if r.get("review") not in ("pending", "n/a"))
    print(f"reviewed {n}/{sum(1 for r in triage['units'] if r['category'] != 'deleted')} → {tpath}")


if __name__ == "__main__":
    main()
