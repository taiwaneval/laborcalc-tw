import random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sampling import pick_weighted, allocate_quota


def test_pick_weighted_is_deterministic_and_respects_weights():
    pairs = [("a", 0.5), ("b", 0.5)]
    r1, r2 = random.Random(1), random.Random(1)
    seq1 = [pick_weighted(r1, pairs) for _ in range(20)]
    seq2 = [pick_weighted(r2, pairs) for _ in range(20)]
    assert seq1 == seq2 and set(seq1) == {"a", "b"}
    rng = random.Random(7)
    assert {pick_weighted(rng, [("a", 1.0), ("b", 0.0)]) for _ in range(50)} == {"a"}


def test_allocate_quota_sums_to_n_and_fixes_rounding_on_largest():
    branches = [{"id": "A", "quota": 0.05}, {"id": "B", "quota": 0.15}, {"id": "C", "quota": 0.80}]
    counts = allocate_quota(branches, 201)
    assert sum(counts.values()) == 201
    assert counts["A"] == 10 and counts["B"] == 30 and counts["C"] == 161
    counts = allocate_quota([{"id": "X", "quota": 0.5}, {"id": "Y", "quota": 0.5}], 3)
    assert sum(counts.values()) == 3
