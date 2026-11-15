"""題目多樣性：參數去重、骨架近似重複、刻意配對（pair_id）、模板在分支內平均、manifest 的 diversity 區塊。"""
import importlib.util
import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 尚未進 release 的 family 也一起檢查；公開匯出只帶一個資料集已出貨的 family，不在 repo 裡的就略過
FAMILIES = [f for f in ["overtime_pay", "annual_leave_days", "ban_checksum", "minguo_year"]
            if (ROOT / "silver/families" / f / "spec.yaml").exists()]


def load_generate():
    s = importlib.util.spec_from_file_location("gen_div", ROOT / "gold/generate.py")
    gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen); return gen


gen = load_generate()


@lru_cache(maxsize=None)
def _generated(fid: str, out: str):
    gen.generate(fid, "2099Q1", "public", 200, out_dir=Path(out), as_of="2026-11-10")
    items = [json.loads(l) for l in (Path(out) / f"{fid}.jsonl").read_text().splitlines()]
    man = json.loads((Path(out) / f"{fid}.manifest.json").read_text())
    return items, man


@pytest.fixture(params=FAMILIES)
def generated(request, tmp_path_factory):
    out = tmp_path_factory.getbasetemp() / f"div-{request.param}"
    return (request.param, *_generated(request.param, str(out)))


def _pkey(it):
    return json.dumps(it["params"], sort_keys=True)


def test_params_are_unique_except_inside_a_pair(generated):
    _, items, _ = generated
    by_key = defaultdict(list)
    for it in items:
        by_key[_pkey(it)].append(it)
    for group in by_key.values():
        if len(group) > 1:
            assert len({it["pair_id"] for it in group}) == 1 and group[0]["pair_id"] is not None, \
                [it["item_id"] for it in group]


def test_pairs_share_params_and_branch_but_use_different_templates(generated):
    _, items, man = generated
    pairs = defaultdict(list)
    for it in items:
        if it["pair_id"]:
            pairs[it["pair_id"]].append(it)
    assert len(pairs) >= 15, "配對太少，算不出問法一致性"
    for pid, group in pairs.items():
        assert len(group) >= 2
        assert len({_pkey(it) for it in group}) == 1 and len({it["branch"] for it in group}) == 1
        assert len({it["knobs"]["template"] for it in group}) == len(group), pid
        assert len({it["gold"] for it in group}) == 1
    assert man["diversity"]["pairs"] == len(pairs)


def test_controls_are_not_paired(generated):
    _, items, _ = generated
    assert all(it["pair_id"] is None for it in items if it["knobs"]["template"].startswith("Z"))


def test_skeleton_masks_names_and_numbers():
    a = gen.skeleton("陳怡君月薪36,000元，休息日加班3小時。", "陳怡君")
    b = gen.skeleton("林家豪月薪4萬2千元，休息日加班十二小時。", "林家豪")
    assert a == "◯月薪#元，休息日加班#小時。"
    assert b == "◯月薪#元，休息日加班#小時。"


def test_a_skeleton_is_reused_at_most_twice_within_a_branch(generated):
    _, items, man = generated
    seen = Counter((it["branch"], gen.skeleton(it["prompt"], it["knobs"]["name"])) for it in items if not it["pair_id"])
    dups = sum(c - 1 for c in seen.values() if c > 1)
    assert dups == man["diversity"]["near_duplicates"]
    assert max(seen.values()) == man["diversity"]["max_skeleton_reuse"] <= gen.MAX_SKELETON_REUSE


def test_templates_are_balanced_within_each_branch(generated):
    _, items, _ = generated
    per_branch = defaultdict(Counter)
    for it in items:
        per_branch[it["branch"]][it["knobs"]["template"]] += 1
    for b, c in per_branch.items():
        assert max(c.values()) - min(c.values()) <= 2, (b, dict(c))


def test_manifest_diversity_block_and_template_share_cap(generated):
    fid, items, man = generated
    d = man["diversity"]
    assert sum(d["template_counts"].values()) == len(items)
    assert d["param_duplicates"] == 0
    assert d["max_template_share"] == max(d["template_counts"].values()) / len(items)
    assert d["max_template_share"] <= gen.MAX_TEMPLATE_SHARE, d["template_counts"]
    regular = [t for t in d["template_counts"] if not t.startswith("Z")]
    assert len(regular) >= 10, f"{fid} 只有 {len(regular)} 個模板"


def test_branch_quotas_are_unchanged_by_pairing(tmp_path):
    gen.generate("annual_leave_days", "2099Q1", "public", 200, out_dir=tmp_path, as_of="2026-11-10")
    cov = json.loads((tmp_path / "annual_leave_days.coverage.json").read_text())
    assert cov["branch"] == {"S0": 10, "S1": 20, "S2": 20, "S3": 20, "S4": 20, "S5": 20, "S6": 20, "S7": 10, "B1": 25, "B2": 25, "B3": 10}
