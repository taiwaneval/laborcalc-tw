"""從 silver/families/<id>/spec.yaml 生成規則卡 HTML。

用法：python3 tools/build_page.py silver/families/overtime_pay [--out site/rules/families]

產出兩個檔案：
  <id>.html           完整 HTML 文件（放到 taiwaneval.org/rules/ 用）
  <id>.artifact.html  無 doctype/head/body 的片段（claude.ai artifact 用）
頁面內嵌 spec 與 rule 的 sha256 與生成時間；spec 一改，hash 就變。
family 狀態與審閱紀錄讀 silver/families/<id>/reviews.yaml（tools/family_reviews.py），不讀 spec.yaml 的 status／review_log。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from family_reviews import HASH_FIELDS, family_status
from paths import TEMPLATES, SITE


def build(family_dir: Path, out_dir: Path) -> list[Path]:
    spec_path = family_dir / "spec.yaml"
    spec_bytes = spec_path.read_bytes()
    spec = yaml.safe_load(spec_bytes)
    sha = hashlib.sha256(spec_bytes).hexdigest()
    fstatus = family_status(family_dir)          # 狀態、目前的 spec／rule hash、全部簽核紀錄
    assert fstatus["spec_sha256"] == sha
    rule_js = (family_dir / spec["implementation"]["js"]).read_text()
    ai_path = family_dir / "ai_review.json"           # tools/ai_review_legal.py 的 AI 意見（審閱輔助，非簽核）
    ai = json.loads(ai_path.read_text()) if ai_path.exists() else None
    reviews_path = family_dir / "reviews.yaml"                # 原文嵌進頁面：簽核表單在它後面接新的一筆，既有紀錄逐字保留
    reviews_raw = reviews_path.read_text() if reviews_path.exists() else ""
    built_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]))
    tpl = env.get_template("rule_card.html.j2")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for full, suffix in ((True, ".html"), (False, ".artifact.html")):
        html = tpl.render(spec=spec, spec_sha256=sha, rule_sha256=fstatus["rule_sha256"], fstatus=fstatus,
                          signed_hashes={k: fstatus[k] for k in HASH_FIELDS}, rule_js=rule_js, ai=ai,
                          ai_stale=bool(ai) and ai.get("spec_sha256") != sha, reviews_raw=reviews_raw,
                          built_at=built_at, full=full)
        p = out_dir / f"{spec['id']}{suffix}"
        p.write_text(html)
        outputs.append(p)
    print("  ".join(f"{k} {fstatus[k][:12]}" for k in HASH_FIELDS) + f"  status {fstatus['status']}")
    for p in outputs:
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    return outputs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("family_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    build(a.family_dir, a.out or (SITE / "rules" / "families"))
