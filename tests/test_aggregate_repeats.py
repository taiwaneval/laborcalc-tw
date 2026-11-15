"""aggregate 的重複執行：同一 (target, family, split) 的多次同版 run 取平均並報範圍；release 需要至少 MIN_REPEATS 次（2026-11-10 起為 1）。

依據 Decision Log〈執行間變異〉：release 評測要重複執行，報告變異幅度。
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("aggregate_repeats_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)

MF = {"n": 4}


def rec(created: str, correct_scored: int, *, n_scored: int = 4, n: int = 4, mismatch=None, fake=None) -> dict:
    return {"log_file": f"{created}.eval", "log_sha256": created, "run_id": created, "created": created,
            "model": "m", "fake": fake, "answer_mode": "direct", "spec_mismatch": mismatch,
            "n": n, "correct": correct_scored, "n_scored": n_scored, "correct_scored": correct_scored,
            "n_control": 0, "correct_control": 0, "items": []}


def test_two_current_runs_are_averaged_and_the_range_is_reported():
    out = agg.pool_runs([rec("2026-11-01", 1), rec("2026-11-02", 3)], MF)
    r = out["repeats"]
    assert r["n_runs"] == 2
    assert r["accuracies"] == [0.25, 0.75]                 # 依 created 排序
    assert r["mean"] == pytest.approx(0.5) and r["min"] == 0.25 and r["max"] == 0.75
    assert r["log_files"] == ["2026-11-01.eval", "2026-11-02.eval"]
    assert out["log_file"] == "2026-11-02.eval"            # items.csv 等仍用最新那一次


def test_stale_runs_are_left_out_when_a_current_run_exists():
    stale = rec("2026-10-09", 4, mismatch={"differs": ["spec_sha256"]})
    out = agg.pool_runs([stale, rec("2026-11-01", 1)], MF)
    assert out["repeats"]["n_runs"] == 1 and out["repeats"]["accuracies"] == [0.25]
    assert out["spec_mismatch"] is None


def test_with_only_stale_runs_the_latest_is_kept_and_still_flagged():
    a = rec("2026-10-08", 1, mismatch={"differs": ["rule_sha256"]})
    b = rec("2026-10-09", 2, mismatch={"differs": ["rule_sha256"]})
    out = agg.pool_runs([a, b], MF)
    assert out["log_file"] == "2026-10-09.eval" and out["spec_mismatch"]
    assert out["repeats"]["n_runs"] == 0                    # 沒有同版 run 可以算重複


def test_incomplete_runs_do_not_count_as_repeats():
    partial = rec("2026-11-03", 1, n=2, n_scored=2)
    out = agg.pool_runs([rec("2026-11-01", 2), partial], MF)
    assert out["repeats"]["n_runs"] == 1 and out["log_file"] == "2026-11-01.eval"


def test_release_needs_one_current_run_per_model_target():
    # 2026-11-10：成本考量改為每個 target 跑一次；有多次時仍取平均並報範圍。
    assert agg.MIN_REPEATS == 1
    stale = agg.pool_runs([rec("2026-10-09", 2, mismatch={"differs": ["spec_sha256"]})], MF)
    once = agg.pool_runs([rec("2026-11-01", 2)], MF)
    blockers = agg.release_blockers("2099Q1", {}, {("m@api@x@direct", "f", "public"): stale,
                                                    ("n@api@x@direct", "f", "public"): once})
    assert any("m@api@x@direct" in b for b in blockers)
    assert not any("n@api@x@direct" in b for b in blockers)


def test_fake_solvers_are_exempt_from_the_repeat_requirement():
    once = agg.pool_runs([rec("2026-11-01", 4, fake="oracle")], MF)
    assert agg.release_blockers("2099Q1", {}, {("fake-oracle@none@none@direct", "f", "public"): once}) == []
