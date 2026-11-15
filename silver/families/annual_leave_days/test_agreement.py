"""出貨前檢查：
1. golden cases 全過 Python 實作
2. golden cases 全過 JS 實作（Node）
3. 隨機 N 組合法輸入，Python 與 JS 答案逐一相同（N-version agreement）
4. 性質：同一到職日，查詢日逐日遞增時日數不減，且只在滿期日（entitlement_date）當天跳階
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rule  # noqa: E402

RUNNER = """
const R = require(process.argv[2]);
let s=''; process.stdin.on('data', d => s += d).on('end', () => {
  const cases = JSON.parse(s);
  process.stdout.write(JSON.stringify(cases.map(c => {
    try { return R.compute(c.hire_date, c.query_date).total; } catch (e) { return 'ERR'; }
  })));
});
"""

# 不存在的日期與非 YYYY-MM-DD 寫法：兩份實作都必須報錯（Python ValueError、JS throw → 'ERR'）
INVALID_DATES = ["2023-02-30", "2023-02-29", "2023-04-31", "2023-13-01", "2023-00-10", "2023-01-00",
                 "20231015", "2023-1-5", "2023-01-05T00:00"]


def run_js(cases: list[dict]) -> list[int]:
    runner = HERE / ".runner.js"
    runner.write_text(RUNNER)
    out = subprocess.run(
        ["node", str(runner), str(HERE / "rule.js")],
        input=json.dumps(cases), capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def rand_date(rng: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def main(n_random: int = 2000, seed: int = 20261009) -> int:
    spec = yaml.safe_load((HERE / "spec.yaml").read_text())
    golden = spec["golden_cases"]
    fails = 0

    # 1. golden vs Python
    for g in golden:
        got = rule.compute(**g["input"])["total"]
        ok = got == g["expected"]
        fails += not ok
        print(f"[py ] {g['id']} {'OK ' if ok else 'FAIL'} expected={g['expected']} got={got} ({g['status']})")

    # 2. golden vs JS
    for g, got in zip(golden, run_js([g["input"] for g in golden])):
        ok = got == g["expected"]
        fails += not ok
        print(f"[js ] {g['id']} {'OK ' if ok else 'FAIL'} expected={g['expected']} got={got}")

    # 3. random agreement（到職日在查詢日往前 32 年內；查詢日在 spec 範圍）
    q_lo, q_hi = date.fromisoformat(spec["parameters"]["query_date"]["min"]), date.fromisoformat(spec["parameters"]["query_date"]["max"])
    rng = random.Random(seed)
    cases = []
    for _ in range(n_random):
        q = rand_date(rng, q_lo, q_hi)
        h = rand_date(rng, q - timedelta(days=32 * 366), q)
        cases.append({"hire_date": h.isoformat(), "query_date": q.isoformat()})
    py = [rule.compute(**c)["total"] for c in cases]
    js = run_js(cases)
    mism = [(c, p, j) for c, p, j in zip(cases, py, js) if p != j]
    fails += len(mism)
    print(f"[agr] random n={n_random} seed={seed} mismatches={len(mism)}")
    for c, p, j in mism[:10]:
        print("      ", c, "py=", p, "js=", j)

    # 4. 無效日期：兩份實作都要拒絕（到職日、查詢日兩個位置都測）
    bad_cases = [{"hire_date": d, "query_date": "2026-06-30"} for d in INVALID_DATES] + \
                [{"hire_date": "2000-01-01", "query_date": d} for d in INVALID_DATES]
    py_bad = []
    for c in bad_cases:
        try:
            rule.compute(**c); py_bad.append("OK")
        except ValueError:
            py_bad.append("ERR")
    js_bad = run_js(bad_cases)
    not_rejected = [(c, p, j) for c, p, j in zip(bad_cases, py_bad, js_bad) if (p, j) != ("ERR", "ERR")]
    fails += len(not_rejected)
    print(f"[bad] invalid dates n={len(bad_cases)} not_rejected_by_both={len(not_rejected)}")
    for c, p, j in not_rejected[:10]:
        print("      ", c, "py=", p, "js=", j)

    # 5. 性質：逐日掃描，日數不減，且只在滿期日當天變動
    viol = jumps_off = 0
    for h in (date(2020, 1, 15), date(2024, 2, 29), date(2023, 8, 31), date(1995, 12, 31)):
        prev, q = -1, max(h, q_lo)
        while q <= q_hi:
            t = rule.compute(h.isoformat(), q.isoformat())["total"]
            viol += t < prev
            if prev >= 0 and t != prev:
                jumps_off += rule.entitlement_date(h, rule.completed_months(h, q)) != q
            prev, q = t, q + timedelta(days=1)
    fails += viol + jumps_off
    print(f"[prop] monotonic violations={viol} jumps_not_on_entitlement_date={jumps_off}")

    print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
