import importlib.util, random, sys, types
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
FAM = ROOT / "silver/families/annual_leave_days"


def load(name, alias=None):
    s = importlib.util.spec_from_file_location(alias or f"al_{name}_under_test", FAM / f"{name}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


smp, rule = load("sampler"), load("rule")


def load_generate():
    s = importlib.util.spec_from_file_location("gen_for_sampler_tests", ROOT / "gold/generate.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
spec = yaml.safe_load((FAM / "spec.yaml").read_text())
tpl = yaml.safe_load((FAM / "templates.yaml").read_text())
Q_LO = date.fromisoformat(spec["parameters"]["query_date"]["min"])
Q_HI = date.fromisoformat(spec["parameters"]["query_date"]["max"])


def test_format_date_styles():
    d = date(2023, 3, 15)
    assert smp.format_date(d, "roc_full") == "民國112年3月15日"
    assert smp.format_date(d, "roc_dot") == "112.3.15"
    assert smp.format_date(d, "ce_full") == "2023年3月15日"
    assert smp.format_date(d, "ce_slash") == "2023/3/15"


def test_plan_quota_and_pool():
    plan = smp.plan(spec, 200, random.Random(0))
    assert len(plan) == 200
    c = Counter(p["branch"] for p in plan)
    assert c["S0"] == 10 and c["B1"] == 25 and c["B2"] == 25 and c["B3"] == 10
    assert all(p["template_pool"] == ("zero_templates" if p["branch"] == "S0" else "templates") for p in plan)
    assert all(p["tier_months"] in spec["boundary_values"]["b3_tier_months"] for p in plan if p["branch"] == "B3")


def test_sampled_params_land_in_their_branch():
    plan = smp.plan(spec, 400, random.Random(1))
    for i, p in enumerate(plan):
        params = smp.sample(random.Random(i), p)
        h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
        assert Q_LO <= q <= Q_HI and h <= q and (q - h).days <= 32 * 366, (p, params)
        m = rule.completed_months(h, q)
        b = next(b for b in spec["branches"] if b["id"] == p["branch"])
        if b["kind"] == "tier":
            assert b["months"][0] <= m <= b["months"][1], (p, params, m)
        elif b["boundary"] == "on":
            assert m == p["tier_months"] and rule.entitlement_date(h, m) == q, (p, params)
        elif b["boundary"] == "before":
            assert m == p["tier_months"] - 1 and rule.entitlement_date(h, m + 1) == q + timedelta(days=1), (p, params)
        else:  # no_corresponding_day
            e = rule.entitlement_date(h, p["tier_months"])
            assert h.day >= 29 and e.day == 1 and q in (e, e - timedelta(days=1)), (p, params)


def test_b3_every_anchor_has_candidates_and_both_sides():
    for k in spec["boundary_values"]["b3_tier_months"]:
        sides = Counter()
        for i in range(40):
            params = smp.sample(random.Random(i), {"branch": "B3", "kind": "boundary", "boundary": "no_corresponding_day",
                                                   "tier_months": k, "template_pool": "templates", **smp.ranges(spec)})
            h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
            sides["new" if rule.entitlement_date(h, k) == q else "old"] += 1
        assert sides["new"] > 0 and sides["old"] > 0, (k, sides)


def test_sampler_ignores_a_foreign_cached_rule_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "rule", types.ModuleType("rule"))   # 另一個 family 的 rule.py 已以裸名稱 import
    smp2 = load("sampler", alias="al_sampler_reloaded")
    params = smp2.sample(random.Random(0), {"branch": "S2", "kind": "tier", "months": [12, 23], "template_pool": "templates", **smp2.ranges(spec)})
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    assert 12 <= rule.completed_months(h, q) <= 23


def test_visible_mixed_styles_and_knobs_are_presentation_only():
    gen = load_generate()
    params = {"hire_date": "2023-03-15", "query_date": "2026-03-14"}
    _, knobs = smp.context(random.Random(5), params, tpl)
    assert set(knobs) == {"hire_style", "query_style"}
    h, q = date.fromisoformat(params["hire_date"]), date.fromisoformat(params["query_date"])
    fields = {"hire": smp.format_date(h, "roc_full"), "query": smp.format_date(q, "ce_slash")}   # 民國 + 西元混用
    assert fields == {"hire": "民國112年3月15日", "query": "2026/3/14"}
    for t in tpl["templates"] + tpl["zero_templates"]:
        prompt = t["text"].format(name="王小明", **fields) + "\n\n" + tpl["answer_instruction"]
        assert gen.gate(prompt, smp.visible(params, fields)) == [], t["id"]


def test_boundary_anchors_are_spread_and_recorded():
    plan = smp.plan(spec, 200, random.Random(0))
    for bid in ("B1", "B2"):
        ks = Counter(p["knobs"]["anchor_months"] for p in plan if p["branch"] == bid)
        assert set(ks) == set(spec["boundary_values"]["tier_months"]) and min(ks.values()) >= 3, (bid, ks)
    b3 = [p["knobs"]["anchor_months"] for p in plan if p["branch"] == "B3"]
    assert len(b3) == 10 and set(b3) <= set(spec["boundary_values"]["b3_tier_months"])
    assert all("knobs" not in p for p in plan if p["kind"] == "tier")
    assert all(p["knobs"]["anchor_months"] == p["tier_months"] for p in plan if p["kind"] == "boundary")


def test_coverage_keys_report_days():
    assert smp.coverage_keys({"hire_date": "2023-03-15", "query_date": "2024-03-15"}) == {"days": "7"}
