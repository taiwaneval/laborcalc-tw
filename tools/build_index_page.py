"""index/<release>/index.json → site/index/<release>.html（與 .artifact.html）。

用法：python3 tools/build_index_page.py 2026Q4
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from paths import FAMILIES, ROOT, SITE, TEMPLATES

INDEX = ROOT / "index"


def _function_doc(path: Path, name: str) -> str | None:
    """不執行檔案，只讀出函式的 docstring。"""
    if not path.exists():
        return None
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node)
    return None


def family_meta(families: list[str]) -> list[dict]:
    """每個 family 的對照分支（spec.yaml）與 naive 假 solver 的錯法（hypotheses.py），頁面上的說明從這裡來，不寫死。"""
    out = []
    for fam in families:
        fdir = FAMILIES / fam
        spec = yaml.safe_load((fdir / "spec.yaml").read_text())
        out.append({
            "id": fam, "name": spec.get("name", fam),
            "controls": [{"id": b["id"], "note": b.get("note", "")} for b in spec.get("branches", []) if b.get("control")],
            "naive": _function_doc(fdir / "hypotheses.py", "naive_answer"),
        })
    return out


def build(release: str) -> list[Path]:
    idx = json.loads((INDEX / release / "index.json").read_text())

    # ranking: public 分數由高到低；unavailable 排最後
    ranking = []
    for name, t in idx["targets"].items():
        pub = t["splits"].get("public", {})
        prv = t["splits"].get("private", {})
        fake = next((v.get("fake") for v in pub.get("families", {}).values() if v.get("fake")), None)
        log_sha = next((v.get("log_sha256") for v in pub.get("families", {}).values()), "")
        answer_mode = next((v.get("answer_mode") for v in pub.get("families", {}).values() if v.get("answer_mode")), "direct")
        n_scored = sum(v.get("n_scored", v.get("n", 0)) for v in pub.get("families", {}).values())
        ranking.append({
            "name": name, "fake": fake, "answer_mode": answer_mode,
            "public": pub.get("score"), "private": prv.get("score"), "gap": t.get("public_private_gap"),
            "control_pass": pub.get("control_pass"), "n_control": pub.get("n_control", 0), "n_scored": n_scored,
            "family_scores": pub.get("family_scores") or {},
            "status": pub.get("status", "unavailable"),
            "reason": ", ".join(f"{k}: {', '.join(v)}" for k, v in (pub.get("reason") or {}).items() if v),
            "log_sha256": log_sha or "",
        })
    ranking.sort(key=lambda r: (r["public"] is None, -(r["public"] or 0)))

    # per-family branch table across targets (public split)
    family_tables = []
    for fam in idx["families"]:
        targets = [r["name"] for r in ranking if fam in idx["targets"][r["name"]]["splits"].get("public", {}).get("families", {})]
        if not targets:
            continue
        first = idx["targets"][targets[0]]["splits"]["public"]["families"][fam]
        branches = []
        for bid, bv in first["by_branch"].items():
            vals = [idx["targets"][t]["splits"]["public"]["families"][fam]["by_branch"].get(bid, {}).get("accuracy", 0.0) for t in targets]
            branches.append({"id": bid, "n": bv["n"], "control": bool(bv.get("control")), "vals": vals})
        fams = [idx["targets"][t]["splits"]["public"]["families"][fam] for t in targets]
        totals = [v["accuracy"] for v in fams]
        controls = [v.get("control_pass") for v in fams]
        family_tables.append({
            "family": fam, "n": first["n"], "n_scored": first.get("n_scored", first["n"]), "n_control": first.get("n_control", 0),
            "targets": targets, "modes": [v.get("answer_mode", "direct") for v in fams],
            "branches": branches, "totals": totals, "controls": controls,
        })
    real_targets = [r["name"] for r in ranking if not r["fake"]]

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]))
    tpl = env.get_template("index_release.html.j2")
    out_dir = SITE / "index"
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for full, suffix in ((True, ".html"), (False, ".artifact.html")):
        html = tpl.render(idx=idx, ranking=ranking, family_tables=family_tables, real_targets=real_targets,
                          family_meta=family_meta(idx["families"]), full=full)
        p = out_dir / f"{release}{suffix}"
        p.write_text(html)
        outs.append(p)
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    return outs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("release")
    a = ap.parse_args()
    build(a.release)
