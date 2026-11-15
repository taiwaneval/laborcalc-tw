from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
FAM = ROOT / "silver/families/annual_leave_days"


def test_spec_shape():
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    assert spec["id"] == "annual_leave_days" and spec["tier"] == 1 and spec["version"] == "0.1.3"
    assert (spec["answer"]["type"], spec["answer"]["tolerance"]) == ("integer_days", 0)
    assert spec["implementation"]["arg_order"] == ["hire_date", "query_date"]
    ids = [b["id"] for b in spec["branches"]]
    assert ids == ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "B1", "B2", "B3"]
    assert abs(sum(b["quota"] for b in spec["branches"]) - 1.0) < 1e-9
    s0 = spec["branches"][0]
    assert s0["control"] is True and s0["template_pool"] == "zero_templates"
    assert spec["parameters"]["query_date"]["min"] == "2018-03-01"
    assert len(spec["golden_cases"]) >= 12
    # B3 只能錨在「目標月可能沒有相當日」的年資；24 年（288 月）在曆法上不可能（見 spec 註解）
    assert 288 not in spec["boundary_values"]["b3_tier_months"]
    assert set(spec["boundary_values"]["b3_tier_months"]) <= set(spec["boundary_values"]["tier_months"])


def test_templates_shape():
    tpl = yaml.safe_load((FAM / "templates.yaml").read_text())
    assert tpl["answer_instruction"] == "請只回答一個整數（天數），不要附任何單位或說明。"
    for t in tpl["templates"] + tpl["zero_templates"]:
        assert "{hire}" in t["text"] and "{query}" in t["text"] and "{name}" in t["text"]
    assert len(tpl["templates"]) >= 10 and len(tpl["zero_templates"]) >= 3


def test_inventory_maps_family_to_unit_38_1():
    fams = yaml.safe_load((ROOT / "silver/inventory/N0030001/families.yaml").read_text())["families"]
    f = next(f for f in fams if f["id"] == "annual_leave_days")
    assert [u["unit_id"] for u in f["units"]] == ["N0030001/38/1"]


def test_paragraph_level_claim_cites_its_source():
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    lb = next(b for b in spec["legal_basis"] if b["id"] == "LSA-38-1")
    assert "未修正" in lb["note"] and "條文對照表" in lb["note"] and "未逐項核對" not in lb["note"]
    assert "verify" not in lb


def test_start_day_rule_is_grounded_in_the_enforcement_rules():
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    art5 = next(b for b in spec["legal_basis"] if b["id"] == "LSAER-5")
    assert "自受僱當日起算" in art5["summary"] and "pcode=N0030002&flno=5" in art5["url"]
    cc = next(b for b in spec["legal_basis"] if b["id"] == "CC-121")
    assert "verify" not in cc and "但書" in cc["note"]


def test_no_corresponding_day_is_verified_against_the_mol_calculator():
    """Calvin 2026-10-10 確認：無函釋，以勞動部特休試算系統核對；勞委會函釋明示勞基法期間適用民法 §121。"""
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    cc = next(b for b in spec["legal_basis"] if b["id"] == "CC-121")
    assert "calcr2.mol.gov.tw/RestDays" in cc["note"] and "0048187" in cc["note"] and "docs/verification" in cc["note"]
    assert spec["open_questions"] == []
    by_id = {g["id"]: g for g in spec["golden_cases"]}
    assert {by_id[i]["status"] for i in ("G11", "G12", "G13", "G14")} == {"calculator-verified"}


def test_official_worked_examples_are_golden_cases():
    spec = yaml.safe_load((FAM / "spec.yaml").read_text())
    official = {(g["input"]["hire_date"], g["input"]["query_date"]): g["expected"] for g in spec["golden_cases"]
                if g["status"] == "published-example" and ("新北市" in g["source"] or "臺北市" in g["source"])}
    assert official == {("2016-10-01", "2017-04-01"): 3, ("2016-10-01", "2017-10-01"): 7,
                        ("2016-02-01", "2017-01-01"): 3, ("2016-02-01", "2017-02-01"): 7}
