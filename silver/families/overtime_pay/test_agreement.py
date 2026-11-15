"""出貨前檢查：
1. golden cases 全過 Python 實作
2. golden cases 全過 JS 實作（Node）
3. 隨機 N 組合法輸入，Python 與 JS 答案逐一相同（N-version agreement）
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rule  # noqa: E402

RUNNER = """
const R = require(process.argv[2]);
let s=''; process.stdin.on('data', d => s += d).on('end', () => {
  const cases = JSON.parse(s);
  process.stdout.write(JSON.stringify(cases.map(c => R.compute(c.monthly_salary, c.day_type, c.hours).total)));
});
"""


def run_js(cases: list[dict]) -> list[int]:
    runner = HERE / ".runner.js"
    runner.write_text(RUNNER)
    out = subprocess.run(
        ["node", str(runner), str(HERE / "rule.js")],
        input=json.dumps(cases), capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def main(n_random: int = 1000, seed: int = 20261008) -> int:
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
    js = run_js([g["input"] for g in golden])
    for g, got in zip(golden, js):
        ok = got == g["expected"]
        fails += not ok
        print(f"[js ] {g['id']} {'OK ' if ok else 'FAIL'} expected={g['expected']} got={got}")

    # 3. random agreement
    rng = random.Random(seed)
    lo = spec["parameters"]["monthly_salary"]["min"]
    hi = spec["parameters"]["monthly_salary"]["max"]
    cases = []
    for _ in range(n_random):
        dt = rng.choice(rule.DAY_TYPES)
        cases.append({
            "monthly_salary": rng.randint(lo, hi),
            "day_type": dt,
            "hours": rng.randint(0, rule.MAX_HOURS[dt]),
        })
    py = [rule.compute(**c)["total"] for c in cases]
    js = run_js(cases)
    mism = [(c, p, j) for c, p, j in zip(cases, py, js) if p != j]
    fails += len(mism)
    print(f"[agr] random n={n_random} seed={seed} mismatches={len(mism)}")
    for c, p, j in mism[:10]:
        print("      ", c, "py=", p, "js=", j)

    # property: 時數增加，金額不減
    viol = 0
    for dt in rule.DAY_TYPES:
        for m in (lo, 36000, 54321, hi):
            prev = -1
            for h in range(rule.MAX_HOURS[dt] + 1):
                t = rule.compute(m, dt, h)["total"]
                viol += t < prev
                prev = t
    fails += viol
    print(f"[prop] monotonic-in-hours violations={viol}")

    print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
