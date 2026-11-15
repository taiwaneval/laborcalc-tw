"""Repo 目錄約定（Medallion 分層）。所有腳本從這裡取路徑，不各自硬寫。

bronze/<pcode>/            法規快照，只進不改：source.json、provenance.json
silver/inventory/<pcode>/  盤點與複核：inventory.json、llm_labels.json、triage.yaml、families.yaml、reviews/*.yaml、prompts/
silver/families/<id>/      規則 family：spec.yaml、rule.py、rule.js、test_agreement.py、reviews.yaml（人工簽核，見 family_reviews.py）
gold/releases/<release>/<split>/<family>.jsonl   生成題集（public 進 repo；private 不進）
site/                      渲染出的頁面（CI 產物）
docs/                      架構圖等文件
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "bronze"
SILVER = ROOT / "silver"
INVENTORY = SILVER / "inventory"
FAMILIES = SILVER / "families"
GOLD = ROOT / "gold"
RELEASES = GOLD / "releases"
SITE = ROOT / "site"
TEMPLATES = ROOT / "tools" / "templates"


def bronze_dir(pcode: str) -> Path:
    return BRONZE / pcode


def inventory_dir(pcode: str) -> Path:
    return INVENTORY / pcode


def family_dir(fid: str) -> Path:
    return FAMILIES / fid
