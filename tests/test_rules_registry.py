import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval" / "tasks"))
import rules_registry as rr  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry  # noqa: E402

REG = dataset_registry.load()
AL, OT = REG.latest_release_of_family("annual_leave_days"), REG.latest_release_of_family("overtime_pay")


def test_load_samples_accepts_items_without_rate_table():
    al = rr.load_samples(AL, "public", "annual_leave_days")
    assert len(al) == 200 and "rate_table" not in al[0].metadata
    assert al[0].metadata["params"].keys() == {"hire_date", "query_date"}
    ot = rr.load_samples(OT, "public", "overtime_pay")
    assert ot[0].metadata["rate_table"]["id"] == "minimum_wage"


def test_cot_rewrite_works_for_annual_leave():
    cot = rr.load_samples(AL, "public", "annual_leave_days", "cot")
    assert all(s.input.endswith(rr.COT_INSTRUCTION) for s in cot)


def test_overtime_cot_prompts_keep_the_rounding_rule():
    """cot 改寫會換掉題幹結尾的說明。加班費 0.2.0 起無條件進位，cot 題也要寫明；
    否則照慣例四捨五入的回答，在 0.2.0 題集的 190 題計分題中有 87 題會被判錯。"""
    cot = rr.load_samples(OT, "public", "overtime_pay", "cot")
    assert all("無條件進位" in s.input and "「答案：<整數>」" in s.input for s in cot)
    direct = rr.load_samples(OT, "public", "overtime_pay", "direct")
    assert all("無條件進位" in s.input for s in direct)


# release 評測一律開最高推理；每個 provider 的開法不同，模式名寫進 metadata。
def test_max_reasoning_modes_send_the_provider_specific_body():
    assert rr.THINKING_BODIES["max_openrouter"] == {"reasoning": {"effort": "max"}}
    assert rr.THINKING_BODIES["enable_dashscope"] == {"enable_thinking": True}
    assert rr.THINKING_BODIES["enable"] == {"chat_template_kwargs": {"enable_thinking": True}}


# gpt-oss 最高只到 high；OpenRouter 各家精度不同，鎖在原生 MXFP4（fp4）的供應商，不准退回別家。
def test_gpt_oss_on_openrouter_uses_high_effort_and_pins_mxfp4_providers():
    body = rr.THINKING_BODIES["high_openrouter_mxfp4"]
    assert body["reasoning"] == {"effort": "high"}
    assert body["provider"]["quantizations"] == ["fp4"]
    assert body["provider"]["allow_fallbacks"] is False
    assert body["provider"]["order"]


def test_task_carries_the_reasoning_body_and_records_the_mode():
    t = rr.rules_registry(family="overtime_pay", thinking="max_openrouter")
    assert t.config.extra_body == {"reasoning": {"effort": "max"}}
    assert t.metadata["thinking"] == "max_openrouter"
