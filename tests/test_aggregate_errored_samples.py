"""沒有分數的題（逾時、被取消）算答錯，並記下原因。

推理很長的模型會有題目在時限內寫不完；那是部署時真實會發生的失敗，照實計為錯，而不是把整個 run 丟掉。
log 狀態為 error／cancelled 但每題都有紀錄（有分數或有錯誤）時仍採用；started（寫到一半）不採用。
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("aggregate_errored_under_test", ROOT / "eval/aggregate.py")
agg = importlib.util.module_from_spec(_s); _s.loader.exec_module(agg)


def sample(i, *, value=None, error=None, branch="W1"):
    scores = {"integer_match": NS(value=value, answer="1")} if value else {}
    return NS(id=f"x/{i}", scores=scores, error=NS(message=error) if error else None,
              metadata={"branch": branch, "pair_id": None}, target="1")


def test_errored_samples_count_as_wrong_with_the_reason_kept():
    items = agg.sample_items([sample(1, value="C"), sample(2, value="I"),
                              sample(3, error="RetryError(<... raised APITimeoutError>)")], controls=set())
    assert [i["correct"] for i in items] == [True, False, False]
    assert items[2]["error"] == "timeout" and items[2]["answer"] is None
    assert items[0]["error"] is None


def test_cancelled_samples_are_labelled_cancelled():
    (it,) = agg.sample_items([sample(1, error="CancelledError('Cancelled via cancel scope')")], controls=set())
    assert it["error"] == "cancelled" and it["correct"] is False


def test_log_status_rules():
    assert agg.usable_log_status("success") and agg.usable_log_status("error") and agg.usable_log_status("cancelled")
    assert not agg.usable_log_status("started")


def test_a_question_retried_after_an_error_counts_once():
    """Inspect 在 retry_on_error 時，同一題每次嘗試各留一筆 sample。題數要照題目算，不能照嘗試次數算：
    否則 200 題的 log 會被算成 210 筆，run 被標成 incomplete（2026-11 的 Haiku 5.5、gemma-4-12B、qwen3.8-flash）。"""
    items = agg.sample_items([
        sample(1, value="C"),
        sample(2, error="CancelledError()"), sample(2, value="C"),       # 第一次被取消，重試答對
        sample(3, value="I"),
        sample(4, error="CancelledError()"), sample(4, error="RetryError(<... raised APITimeoutError>)"),  # 兩次都失敗
    ], controls=set())
    assert [i["item_id"] for i in items] == ["x/1", "x/2", "x/3", "x/4"]
    assert [i["correct"] for i in items] == [True, True, False, False]
    assert items[1]["error"] is None and items[1]["answer"] == "1"
    # 沒有任何一次有分數的題仍算答錯，原因記最後一次嘗試的
    assert items[3]["error"] == "timeout" and items[3]["answer"] is None


def test_a_scored_attempt_is_kept_whatever_its_position_in_the_log():
    items = agg.sample_items([sample(1, value="I"), sample(1, error="CancelledError()")], controls=set())
    assert len(items) == 1 and items[0]["correct"] is False and items[0]["error"] is None and items[0]["answer"] == "1"
