"""問法一致性：同一組參數、不同模板的配對題，模型答對與否是否一致。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("aggregate_phrasing_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)
sys.path.insert(0, str(ROOT / "eval/tasks"))
import rules_registry as rr  # noqa: E402


def it(pid, correct):
    return {"item_id": "x", "branch": "B", "control": False, "correct": correct, "pair_id": pid}


def test_consistency_counts_pairs_with_the_same_outcome():
    items = [it("p1", True), it("p1", True), it("p2", False), it("p2", False), it("p3", True), it("p3", False),
             it(None, True), it("p4", True)]                       # p4 少一題：不計
    c = agg.phrasing_consistency(items)
    assert c == {"pairs": 3, "consistent": 2, "rate": 2 / 3, "both_correct": 1, "both_wrong": 1, "flipped": 1}


def test_no_pairs_gives_none():
    assert agg.phrasing_consistency([it(None, True)]) is None


def test_pair_id_reaches_the_eval_sample_metadata():
    assert "pair_id" in rr.METADATA_KEYS
