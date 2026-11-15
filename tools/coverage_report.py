"""從 silver/inventory + bronze provenance 生成 coverage 報告頁。

用法：python3 tools/coverage_report.py N0030001 [--out site/rules/laws]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from paths import bronze_dir, inventory_dir, SITE, TEMPLATES
CATEGORIES = ["computable", "lookup", "definitional", "discretionary", "delegated"]


def build(pcode: str, out_dir: Path) -> list[Path]:
    d = inventory_dir(pcode)
    prov = json.loads((bronze_dir(pcode) / "provenance.json").read_text())
    prov["pcode"] = pcode
    triage = yaml.safe_load((d / "triage.yaml").read_text())
    inv = {u["unit_id"]: u for u in json.loads((d / "inventory.json").read_text())}
    fam_doc = yaml.safe_load((d / "families.yaml").read_text()) if (d / "families.yaml").exists() else {"families": []}
    families = fam_doc["families"]

    encoded: dict[str, dict] = {}
    for f in families:
        for u in f["units"]:
            encoded[u["unit_id"]] = {"family": f["id"], "coverage": u["coverage"], "note": u.get("note")}

    units = []
    for r in triage["units"]:
        if r["category"] == "deleted":
            continue
        r = dict(r)
        r["text"] = inv[r["unit_id"]]["text"]
        r["encoded"] = encoded.get(r["unit_id"])
        units.append(r)

    cat = Counter(r["category"] for r in units)
    for c in CATEGORIES:
        cat.setdefault(c, 0)
    computable = [r for r in units if r["category"] == "computable"]
    stats = {
        "articles": len({r["article"] for r in units}),
        "live": len(units),
        "cat": dict(cat),
        "computable_articles": len({r["article"] for r in computable}),
        "encoded_full": sum(1 for r in computable if r["encoded"] and r["encoded"]["coverage"] == "full"),
        "encoded_partial": sum(1 for r in computable if r["encoded"] and r["encoded"]["coverage"] == "partial"),
        "reviewed": sum(1 for r in units if r["review"] != "pending"),
        "overridden": sum(1 for r in units if r["review"] == "overridden"),
        "conflicts": sum(1 for r in units if r["conflicts"]),
        "conflicts_open": sum(1 for r in units if r["conflicts"] and r["review"] == "pending"),
    }

    chapters: "OrderedDict[str, dict]" = OrderedDict()
    for r in units:
        ch = chapters.setdefault(r["chapter"], {"name": r["chapter"], "counts": Counter(), "total": 0})
        ch["counts"][r["category"]] += 1
        ch["total"] += 1
    chapter_list = []
    for ch in chapters.values():
        ch["counts"] = {c: ch["counts"].get(c, 0) for c in CATEGORIES}
        chapter_list.append(ch)

    built_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]))
    tpl = env.get_template("coverage_report.html.j2")
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for full, suffix in ((True, ".html"), (False, ".artifact.html")):
        html = tpl.render(prov=prov, triage=triage, stats=stats, families=families, units=units,
                          computable=computable, chapters=chapter_list, categories=CATEGORIES,
                          built_at=built_at, full=full)
        p = out_dir / f"coverage_{pcode}{suffix}"
        p.write_text(html)
        outs.append(p)
    print(json.dumps(stats, ensure_ascii=False))
    for p in outs:
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    return outs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pcode")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    build(a.pcode, a.out or (SITE / "rules" / "laws"))
