import importlib.util, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
_s = importlib.util.spec_from_file_location("build_page_under_test", ROOT / "tools/build_page.py")
bp = importlib.util.module_from_spec(_s); _s.loader.exec_module(bp)


def test_annual_leave_card_has_generic_calculator_and_golden_rows(tmp_path):
    bp.build(ROOT / "silver/families/annual_leave_days", tmp_path)
    html = (tmp_path / "annual_leave_days.html").read_text()
    assert 'id="in-hire_date"' in html and 'type="date"' in html and 'id="in-query_date"' in html
    assert html.count("data-args=") == 19                         # 19 golden cases，JS 依 arg_order 呼叫
    assert "AnnualLeaveRule" in html and "monthly_salary" not in html
    assert "已滿月數" in html or "completedMonths" in html          # 試算結果顯示中間量
    assert "民法 §121" in html and "到職日算入" in html             # 公式有渲染
    assert html.count("calculator-verified") >= 4                    # G11–G14 經勞動部特休試算系統核對
    assert "目前沒有待釐清事項" in html                               # open_questions 已清空，要明說
    assert "加班費試算系統" not in html and "pending-official" not in html   # 狀態說明只列本 family 用到的狀態
    assert "自受僱當日起算" in html                                   # 起算點的法源（施行細則第 5 條）在法源表


def test_overtime_card_keeps_its_custom_calculator(tmp_path):
    bp.build(ROOT / "silver/families/overtime_pay", tmp_path)
    html = (tmp_path / "overtime_pay.html").read_text()
    assert 'id="in-monthly"' in html and "R.validate(m, d, h" in html and html.count('data-m="') == 16
    assert "最低工資（月）" in html and "基本工資（月）" not in html              # 最低工資法
    assert "無條件進位到元" in html and "四捨五入到元" not in html                # rounding.final = ceil_to_integer
    assert "MOL-RESTDAY-9H" in html and "0026202" in html                        # 新的法源與 note 都上卡


def test_layout_is_chosen_by_family_id_not_by_parameter_names(tmp_path):
    import shutil, yaml
    src = ROOT / "silver/families/annual_leave_days"
    fam = tmp_path / "synthetic_family"; fam.mkdir()
    shutil.copy(src / "rule.js", fam / "rule.js")
    shutil.copy(src / "rule.py", fam / "rule.py")   # 規則卡要印 rule sha256 作為簽核對象
    for name in ("templates.yaml", "sampler.py"):    # 以及 templates／sampler sha256
        shutil.copy(src / name, fam / name)
    spec = yaml.safe_load((src / "spec.yaml").read_text())
    spec["id"] = "synthetic_family"
    spec["parameters"]["monthly_salary"] = {"type": "int", "min": 1, "max": 2, "note": "不在 arg_order 的同名參數"}
    (fam / "spec.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True))
    out = tmp_path / "out"
    bp.build(fam, out)
    html = (out / "synthetic_family.html").read_text()
    assert 'id="in-hire_date"' in html and 'id="in-monthly"' not in html


# ---- 簽核紀錄改放 reviews.yaml
import family_reviews as fr  # noqa: E402  (tools 已在 sys.path)


def _family_copy(tmp_path, fid="annual_leave_days"):
    import shutil
    dst = tmp_path / fid
    shutil.copytree(ROOT / "silver/families" / fid, dst, ignore=shutil.ignore_patterns("__pycache__", "reviews.yaml"))   # 從未簽核的狀態開始；需要簽核的測試自己寫 reviews.yaml
    return dst


def _reviews(fdir, entries):
    import yaml
    (fdir / "reviews.yaml").write_text(yaml.safe_dump({"reviews": entries}, allow_unicode=True))


def test_card_without_reviews_is_draft_and_prints_all_four_full_hashes_to_sign(tmp_path):
    fdir = _family_copy(tmp_path)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    assert 'data-status="draft"' in html
    for k, v in fr.family_hashes(fdir).items():                        # 四個都整串印出來給審閱者照抄
        assert f"{k}: {v}" in html
    assert "silver/families/annual_leave_days/reviews.yaml" in html
    assert "<code>review_log</code>" not in html                        # 舊流程（寫進 spec.yaml 的 review_log）不再出現


def test_card_with_a_matching_approval_is_reviewed_and_lists_the_reviewer(tmp_path):
    fdir = _family_copy(tmp_path)
    _reviews(fdir, [{"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", **fr.family_hashes(fdir),
                     "verdict": "approved", "notes": "逐條對照法源"}])
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    assert 'data-status="reviewed"' in html and "Calvin Ku" in html and "逐條對照法源" in html
    assert "目前版本" in html


def test_card_marks_a_review_of_an_older_version_as_no_longer_current(tmp_path):
    fdir = _family_copy(tmp_path)
    _reviews(fdir, [{"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", "spec_sha256": "0" * 64,
                     "rule_sha256": "1" * 64, "templates_sha256": "2" * 64, "sampler_sha256": "3" * 64,
                     "verdict": "approved"}])
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    assert 'data-status="draft"' in html and "已失效" in html


def test_string_open_questions_still_render(tmp_path):
    import yaml
    fdir = _family_copy(tmp_path)
    spec = yaml.safe_load((fdir / "spec.yaml").read_text())
    spec["open_questions"] = ["某個邊界的處理待確認"]
    (fdir / "spec.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True))
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    assert "某個邊界的處理待確認" in html and "目前沒有待釐清事項" not in html


def test_overtime_card_legend_lists_its_own_statuses(tmp_path):
    """0.2.0 起 golden 都已對官方範例或勞動部試算系統核對，不再有 pending-official。"""
    bp.build(ROOT / "silver/families/overtime_pay", tmp_path)
    html = (tmp_path / "overtime_pay.html").read_text()
    assert "calculator-verified" in html and "published-example" in html and "pending-official" not in html
    assert "目前沒有待釐清事項" in html


def test_editing_only_the_templates_invalidates_the_review_shown_on_the_card(tmp_path):
    fdir = _family_copy(tmp_path)
    _reviews(fdir, [{"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", **fr.family_hashes(fdir),
                     "verdict": "approved"}])
    with (fdir / "templates.yaml").open("a") as fh:
        fh.write("\n# 只改模板\n")
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    assert 'data-status="draft"' in html and "已失效" in html


# ---- 審閱輔助：法源列的勾選框與 AI 意見；排除項目附理由（2026-10-12）
def _ai_review(fdir, verdicts, spec_sha=None):
    import json
    spec_sha = spec_sha or fr.family_hashes(fdir)["spec_sha256"]
    (fdir / "ai_review.json").write_text(json.dumps({
        "model": "test/model", "reviewed_at": "2026-10-12T10:00:00+08:00", "spec_sha256": spec_sha,
        "entries": {k: {"verdict": v, "reason": f"{k} 的理由"} for k, v in verdicts.items()}}, ensure_ascii=False))


def test_every_legal_basis_row_has_a_reviewer_checkbox(tmp_path):
    import yaml
    fdir = _family_copy(tmp_path, "overtime_pay")
    (fdir / "ai_review.json").unlink(missing_ok=True)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/overtime_pay.html").read_text()
    n = len(yaml.safe_load((fdir / "spec.yaml").read_text())["legal_basis"])
    assert html.count('class="lb-check"') == n
    assert "尚無 AI 意見" in html


def test_ai_opinions_show_symbol_and_reason_and_are_not_a_sign_off(tmp_path):
    fdir = _family_copy(tmp_path, "overtime_pay")
    _ai_review(fdir, {"LSA-24-1": "ok", "MOL-HOURLY": "warn", "LSA-39": "fail"})
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/overtime_pay.html").read_text()
    assert 'data-ai="ok"' in html and 'data-ai="warn"' in html and 'data-ai="fail"' in html
    assert "MOL-HOURLY 的理由" in html and "test/model" in html and "不是人工簽核" in html
    assert 'data-status="draft"' in html


def test_ai_opinions_for_an_older_spec_are_flagged(tmp_path):
    fdir = _family_copy(tmp_path, "overtime_pay")
    _ai_review(fdir, {"LSA-24-1": "ok"}, spec_sha="0" * 64)
    bp.build(fdir, tmp_path / "out")
    assert "針對舊版 spec" in (tmp_path / "out/overtime_pay.html").read_text()


def test_overtime_scope_exclusions_carry_reasons(tmp_path):
    import yaml
    spec = yaml.safe_load((ROOT / "silver/families/overtime_pay/spec.yaml").read_text())
    assert all(isinstance(e, dict) and e["item"] and e["reason"] for e in spec["scope"]["excludes"])
    bp.build(ROOT / "silver/families/overtime_pay", tmp_path)
    html = (tmp_path / "overtime_pay.html").read_text()
    for e in spec["scope"]["excludes"]:
        assert e["item"] in html and e["reason"].strip() in html


# ---- 審閱者簽核表單：只在本機顯示，產生 reviews.yaml 給審閱者自己放進 repo（AI 不代簽） ----

def _signoff_js(html):
    m = re.search(r"// BEGIN reviewsYaml\n(.*?)// END reviewsYaml", html, re.S)
    assert m, "頁面裡找不到 reviewsYaml()"
    return m.group(1)


def _run_reviews_yaml(html, existing, entry):
    import json, subprocess
    script = _signoff_js(html) + "\nprocess.stdout.write(reviewsYaml(JSON.parse(process.argv[1]), JSON.parse(process.argv[2])));"
    out = subprocess.run(["node", "-e", script, json.dumps(existing), json.dumps(entry, ensure_ascii=False)],
                         capture_output=True, text=True, check=True)
    return out.stdout


def test_signoff_form_is_hidden_on_the_public_page_and_carries_the_full_hashes(tmp_path):
    import json
    fdir = _family_copy(tmp_path)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    sec = re.search(r'<section id="signoff"[^>]*>', html).group(0)
    assert " hidden" in sec                                             # 公開頁面預設不顯示
    assert "location.protocol === 'file:'" in html and "has('review')" in html
    hashes = json.loads(re.search(r'<script type="application/json" id="signoff-hashes">(.*?)</script>', html, re.S).group(1))
    assert hashes == fr.family_hashes(fdir)


def _entry(fdir, **kw):
    return {"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-12", **fr.family_hashes(fdir),
            "verdict": "approved", "notes": ""} | kw


def test_generated_reviews_yaml_without_an_existing_file_makes_the_family_reviewed(tmp_path):
    fdir = _family_copy(tmp_path)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    notes = '逐條對照法源: 試算器 "G11" 通過 # 不是註解\n第二行'
    (fdir / "reviews.yaml").write_text(_run_reviews_yaml(html, "", _entry(fdir, notes=notes)))
    st = fr.family_status(fdir)
    assert st["status"] == "reviewed" and st["review"]["notes"] == notes


def test_generated_reviews_yaml_keeps_existing_records_byte_for_byte(tmp_path):
    import yaml
    fdir = _family_copy(tmp_path)
    old = {"reviewer": "Calvin Ku", "role": "owner", "date": "2026-10-11", "spec_sha256": "0" * 64,
           "rule_sha256": "1" * 64, "templates_sha256": "2" * 64, "sampler_sha256": "3" * 64, "verdict": "approved"}
    for existing in ("# 手寫的註解\nreviews:\n  - " + yaml.safe_dump(old, allow_unicode=True).replace("\n", "\n    ").rstrip() + "\n",
                     yaml.safe_dump({"reviews": [old]}, allow_unicode=True)):        # 兩種縮排：手寫 2 格、safe_dump 0 格
        (fdir / "reviews.yaml").write_text(existing)
        bp.build(fdir, tmp_path / "out")
        html = (tmp_path / "out/annual_leave_days.html").read_text()
        out = _run_reviews_yaml(html, existing, _entry(fdir, verdict="changes_requested", notes="LSA-38-1 待補"))
        assert out.startswith(existing)
        (fdir / "reviews.yaml").write_text(out)
        recs = fr.load_reviews(fdir)
        assert [r["date"] for r in recs] == ["2026-10-11", "2026-10-12"] and recs[1]["verdict"] == "changes_requested"


def test_the_page_embeds_the_existing_reviews_file_verbatim(tmp_path):
    import json
    fdir = _family_copy(tmp_path)
    raw = "# 註解 </script>\nreviews: []\n"
    (fdir / "reviews.yaml").write_text(raw)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    blob = re.search(r'<script type="application/json" id="reviews-raw">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob) == raw


def test_appending_to_a_flow_style_reviews_list_is_refused(tmp_path):
    import subprocess, json
    fdir = _family_copy(tmp_path)
    bp.build(fdir, tmp_path / "out")
    html = (tmp_path / "out/annual_leave_days.html").read_text()
    script = _signoff_js(html) + "\ntry { reviewsYaml('reviews: []\\n', JSON.parse(process.argv[1])); process.exit(0) } catch (e) { process.stdout.write(e.message); process.exit(3) }"
    r = subprocess.run(["node", "-e", script, json.dumps(_entry(fdir))], capture_output=True, text=True)
    assert r.returncode == 3 and "reviews.yaml" in r.stdout
