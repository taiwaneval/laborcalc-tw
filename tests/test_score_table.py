"""逐題得分表（eval/scores/<release>/）：從評測紀錄產生，不含模型作答原文；aggregate 只讀這張表也能算出同一份 index。

用暫存目錄裡的假 family 與假紀錄（Inspect 的 read_eval_log 換成回傳假 log），不讀真的評測紀錄、不呼叫模型。
最後兩項檢查已提交的得分表：公開版沒有原始紀錄時也要通過。
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "eval"))
import aggregate as agg  # noqa: E402  — 與 score_table 內部用的是同一個模組物件，monkeypatch 才會生效
import dataset_registry  # noqa: E402
import score_table as st  # noqa: E402

R, FID = "fakeset-2099Q1", "fam"
T1, T2 = "m1@api@p@max", "fake-oracle@none@none@direct"
SECRET_TEXT = "這段是模型作答原文不可以出現在得分表"
VERSION = {"family_version": "0.1.0", "generator_version": "0.6.0"}   # 四個 hash 在 fixture 裡補上


def sample(i, answer, *, branch="A", x=2, error=None, completion=None, pair_id=None, version=None):
    sc = None if error else NS(value="C" if answer == x * 2 else "I", answer=None if answer is None else str(answer))
    return NS(id=f"{FID}/{R}/public/{i:04d}", target=str(x * 2), scores={"integer_match": sc} if sc else {},
              metadata={"branch": branch, "pair_id": pair_id, "params": {"x": x}, **(version or VERSION)},
              output=NS(completion=completion if completion is not None else f"{SECRET_TEXT}：{answer}"), error=error)


def log(samples, target=T1, status="success", created="2099-01-02T00:00:00+00:00", fake=None):
    return NS(status=status, samples=samples,
              eval=NS(model="openai-api/x/m", run_id=f"run-{created}", created=created,
                      metadata={"release": R, "split": "public", "family": FID, "answer_mode": "direct",
                                "thinking": "max_openrouter", "evaluation_target": target, "fake": fake}))


def default_logs():
    return {
        # 第 0 題答對；第 1 題忘了乘 2；第 2 題沒對上任何假說；第 3 題逾時；第 4 題是對照題；第 5 題沒有整數
        "a.eval": log([sample(0, 4), sample(1, 2), sample(2, 7), sample(3, None, error=NS(message="TimeoutError")),
                       sample(4, 0, branch="Z", x=0), sample(5, None, completion=f"{SECRET_TEXT}，不知道")]),
        # 第 0 題 retry 留下兩筆：先逾時、再答對
        "b.eval": log([sample(0, None, error=NS(message="TimeoutError")), sample(0, 4), sample(1, 4), sample(2, 4),
                       sample(3, 4), sample(4, 0, branch="Z", x=0), sample(5, 4)], target=T2, fake="oracle"),
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """一個假 family、一份 public manifest（n=6）、兩份假紀錄；aggregate 與 score_table 都指到這個暫存目錄。"""
    fam = tmp_path / "families" / FID
    fam.mkdir(parents=True)
    (fam / "spec.yaml").write_text("id: fam\ntier: 1\nimplementation: {python: rule.py}\n"
                                   "branches:\n  - {id: A}\n  - {id: Z, control: true}\n")
    (fam / "rule.py").write_text("def answer(p):\n    return p['x'] * 2\n")
    (fam / "templates.yaml").write_text("answer_instruction: 只回答一個整數\n")
    (fam / "sampler.py").write_text("")
    (fam / "hypotheses.py").write_text(
        "ATTRIBUTION_TOL = 0\n\ndef hypotheses(p):\n    return {'correct': p['x'] * 2, 'forgot_double': p['x']}\n")
    import family_reviews as fr
    hashes = fr.family_hashes(fam)
    VERSION.update(hashes)
    pub = tmp_path / "releases" / R / "public"
    pub.mkdir(parents=True)
    (pub / f"{FID}.manifest.json").write_text(json.dumps({
        "family": FID, "family_version": "0.1.0", "family_status": "draft", **hashes, "generator_version": "0.6.0",
        "release": R, "split": "public", "salt": "test", "n": 6, "rejected": 0, "lineage": {"laws": {}}, "as_of": "2099-01-01"}))
    (tmp_path / "datasets.yaml").write_text(yaml.safe_dump({"datasets": {"fakeset": {
        "name": "FakeSet-TW", "families": [FID], "releases": [R], "release_name_format": "fakeset-{period}",
        "public": {"github": "o/fakeset", "huggingface": "o/FakeSet", "templates": "t"}}}}))
    for attr, sub in (("FAMILIES", "families"), ("RELEASES", "releases"), ("LOGS", "logs"), ("INDEX", "index"),
                      ("SCORES", "scores"), ("REGISTRY", "datasets.yaml")):
        monkeypatch.setattr(agg, attr, tmp_path / sub)
    set_logs(tmp_path, monkeypatch, default_logs())
    return tmp_path


def set_logs(repo: Path, monkeypatch, logs: dict) -> None:
    """把假紀錄放進 logs/<release>/（檔案內容是檔名）並讓 read_eval_log 回傳對應的假 log。"""
    import inspect_ai.log
    d = repo / "logs" / R
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.eval"):
        old.unlink()
    for name in logs:
        (d / name).write_bytes(name.encode())
    reader = lambda p, **k: logs[Path(p).name]  # noqa: E731
    monkeypatch.setattr(inspect_ai.log, "read_eval_log", reader)
    monkeypatch.setattr(agg, "read_eval_log", reader)


def build(repo: Path) -> list[Path]:
    return st.build(R)          # 路徑用 aggregate 的（fixture 已指到暫存目錄）


def rows(repo: Path) -> list[dict]:
    return [json.loads(ln) for ln in (repo / "scores" / R / "items.jsonl").read_text(encoding="utf-8").splitlines()]


def by_item(repo: Path, log_name: str) -> dict[int, dict]:
    sha = hashlib.sha256(log_name.encode()).hexdigest()
    return {int(r["item_id"][-4:]): r for r in rows(repo) if r["log_sha256"] == sha}


def index_without_time(repo: Path) -> dict:
    ix = json.loads((repo / "index" / R / "index.json").read_text(encoding="utf-8"))
    ix.pop("generated_at")
    return ix


# --------------------------------------------------------------------------- 格式

def test_each_row_is_one_target_and_item_with_the_parsed_answer_score_and_attribution(repo):
    build(repo)
    a = by_item(repo, "a.eval")
    assert set(a[0]) == set(st.ITEM_FIELDS)
    assert (a[0]["evaluation_target"], a[0]["family"], a[0]["split"]) == (T1, FID, "public")
    assert a[0]["item_id"] == f"{FID}/{R}/public/0000"
    assert (a[0]["gold"], a[0]["parsed_answer"], a[0]["correct"], a[0]["attribution"]) == (4, 4, True, "correct")
    assert (a[1]["parsed_answer"], a[1]["correct"], a[1]["attribution"]) == (2, False, "forgot_double")
    assert (a[2]["parsed_answer"], a[2]["correct"], a[2]["attribution"]) == (7, False, "unexplained")
    assert (a[3]["parsed_answer"], a[3]["correct"], a[3]["error"], a[3]["attribution"]) == (None, False, "timeout", "unscored")
    assert (a[4]["branch"], a[4]["control"], a[4]["correct"]) == ("Z", True, True)
    assert (a[5]["parsed_answer"], a[5]["correct"], a[5]["error"], a[5]["attribution"]) == (None, False, None, "no_integer")


def test_a_question_that_was_retried_is_one_row_and_the_scored_attempt_wins(repo):
    build(repo)
    b = by_item(repo, "b.eval")
    assert len(b) == 6
    assert (b[0]["correct"], b[0]["error"]) == (True, None)


def test_the_table_never_contains_model_text(repo):
    build(repo)
    for p in (repo / "scores" / R).iterdir():
        assert SECRET_TEXT not in p.read_text(encoding="utf-8"), p.name
    assert "response" not in st.ITEM_FIELDS and "completion" not in st.ITEM_FIELDS


def test_each_run_records_its_log_hash_and_the_fields_the_index_needs(repo):
    build(repo)
    doc = json.loads((repo / "scores" / R / "runs.json").read_text(encoding="utf-8"))
    assert doc["release"] == R and doc["schema_version"] == st.SCHEMA_VERSION
    runs = {r["log_file"]: r for r in doc["runs"]}
    a = runs["a.eval"]
    assert a["log_sha256"] == hashlib.sha256(b"a.eval").hexdigest()
    assert (a["evaluation_target"], a["family"], a["split"], a["answer_mode"], a["thinking"]) == (T1, FID, "public", "direct", "max_openrouter")
    assert (a["model"], a["log_status"], a["fake"]) == ("openai-api/x/m", "success", None)
    assert a["versions"] == [{k: VERSION[k] for k in st.VERSION_FIELDS}]
    assert runs["b.eval"]["fake"] == "oracle"


def test_a_log_still_being_written_is_left_out(repo, monkeypatch):
    logs = default_logs()
    logs["c.eval"] = log([sample(0, 4)], target="m3@api@p@max", status="started")
    set_logs(repo, monkeypatch, logs)
    build(repo)
    assert {r["evaluation_target"] for r in rows(repo)} == {T1, T2}


def test_building_twice_gives_the_same_bytes_and_reading_gives_back_the_same_runs(repo):
    build(repo)
    first = {p.name: p.read_bytes() for p in (repo / "scores" / R).iterdir()}
    assert set(first) == {"runs.json", "items.jsonl"}
    table = st.read(repo / "scores" / R)
    build(repo)
    assert {p.name: p.read_bytes() for p in (repo / "scores" / R).iterdir()} == first
    assert st.render(table, R) == first


def test_an_answer_that_is_not_a_plain_integer_is_refused(repo, monkeypatch):
    """取出的答案必須是整數：別的字串可能帶著原文，而且讀回來的 index 會和由紀錄算出的不同。"""
    logs = default_logs()
    logs["a.eval"].samples[1].scores["integer_match"].answer = "約 2 元"
    set_logs(repo, monkeypatch, logs)
    with pytest.raises(st.ScoreTableError, match="整數"):
        build(repo)
    assert not (repo / "scores" / R).exists()


# --------------------------------------------------------------------------- 答對題數與由紀錄算出的 index 一致

def test_build_fails_and_writes_nothing_when_its_counts_differ_from_the_index_computed_from_the_logs(repo, monkeypatch):
    real = agg.sample_items

    def one_less(samples, controls):
        items = real(samples, controls)
        next(i for i in items if i["correct"] and not i["control"])["correct"] = False
        return items
    monkeypatch.setattr(agg, "sample_items", one_less)
    with pytest.raises(st.ScoreTableError, match="答對"):
        build(repo)
    assert not (repo / "scores" / R).exists()


def test_the_index_from_the_table_alone_is_the_index_from_the_logs(repo):
    agg.aggregate(R)                                     # 只有紀錄
    from_logs = index_without_time(repo)
    items_csv = (repo / "index" / R / "items.csv").read_bytes()
    assert from_logs["targets"][T1]["splits"]["public"]["families"][FID]["correct_scored"] == 1
    build(repo)
    for p in (repo / "logs" / R).glob("*.eval"):         # 公開版：沒有原始紀錄，只有得分表
        p.unlink()
    agg.aggregate(R)
    assert index_without_time(repo) == from_logs
    assert (repo / "index" / R / "items.csv").read_bytes() == items_csv


def test_aggregate_refuses_a_table_that_disagrees_with_the_logs_and_writes_nothing(repo):
    build(repo)
    p = repo / "scores" / R / "items.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[1])
    assert first["correct"] is False
    first["correct"] = True                              # 有人把一題改成答對
    p.write_text("\n".join([lines[0], json.dumps(first, ensure_ascii=False), *lines[2:]]) + "\n", encoding="utf-8")
    with pytest.raises(st.ScoreTableError, match="a.eval"):
        agg.aggregate(R)
    assert not (repo / "index" / R).exists()
    assert agg.main(["--release", R]) == 1               # CLI：結束碼 1，不是 traceback


def test_a_log_added_after_the_table_was_built_is_caught(repo, monkeypatch):
    build(repo)
    logs = default_logs()
    logs["c.eval"] = log([sample(i, 4) for i in range(5)] + [sample(5, 0, branch="Z", x=0)], target="m3@api@p@max")
    set_logs(repo, monkeypatch, logs)
    with pytest.raises(st.ScoreTableError, match="c.eval"):
        agg.aggregate(R)


def test_the_source_can_be_forced_to_the_table_even_when_logs_are_present(repo, monkeypatch):
    build(repo)
    monkeypatch.setattr(agg, "read_eval_log", lambda *a, **k: pytest.fail("source=scores 不應該讀紀錄"))
    ix = agg.aggregate(R, source="scores")
    assert ix["targets"][T1]["splits"]["public"]["families"][FID]["n"] == 6


def test_asking_for_the_table_when_there_is_none_is_an_error(repo):
    with pytest.raises(st.ScoreTableError, match="沒有得分表"):
        agg.aggregate(R, source="scores")


# --------------------------------------------------------------------------- --check

def check(repo: Path) -> list[str]:
    return st.check(R)


def test_check_passes_when_table_logs_and_index_agree(repo):
    build(repo)
    agg.aggregate(R)
    assert check(repo) == []


def test_check_passes_without_logs_when_the_table_matches_the_committed_index(repo):
    build(repo)
    agg.aggregate(R)
    for p in (repo / "logs" / R).glob("*.eval"):
        p.unlink()
    assert check(repo) == []


def test_check_reports_a_table_that_no_longer_matches_the_logs(repo, monkeypatch):
    build(repo)
    agg.aggregate(R)
    logs = default_logs()
    logs["a.eval"].samples[1].scores["integer_match"].answer = "9"
    set_logs(repo, monkeypatch, logs)
    problems = check(repo)
    assert problems and any("items.jsonl" in p for p in problems)


def test_check_reports_a_count_that_differs_from_the_committed_index(repo):
    build(repo)
    agg.aggregate(R)
    ij = repo / "index" / R / "index.json"
    ix = json.loads(ij.read_text(encoding="utf-8"))
    ix["targets"][T1]["splits"]["public"]["families"][FID]["correct_scored"] += 1
    ij.write_text(json.dumps(ix, ensure_ascii=False), encoding="utf-8")
    problems = check(repo)
    assert any(T1 in p and "correct_scored" in p for p in problems)


def test_check_reports_a_run_in_the_index_that_the_table_does_not_have(repo):
    build(repo)
    agg.aggregate(R)
    doc_p = repo / "scores" / R / "runs.json"
    doc = json.loads(doc_p.read_text(encoding="utf-8"))
    doc["runs"] = [r for r in doc["runs"] if r["log_file"] != "b.eval"]
    doc_p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    for p in (repo / "logs" / R).glob("*.eval"):
        p.unlink()
    assert check(repo)


def test_check_without_a_table_says_how_to_build_it(repo):
    assert any("eval/score_table.py" in p for p in check(repo))


# --------------------------------------------------------------------------- 已提交的得分表（每個資料集的最新 release）

def latest_releases() -> list[str]:
    return [d.latest_release for d in dataset_registry.load().datasets if d.latest_release]


@pytest.mark.parametrize("release", latest_releases())
def test_the_committed_table_agrees_with_the_committed_index_and_with_the_logs_when_present(release):
    assert st.check(release) == []


@pytest.mark.parametrize("release", latest_releases())
def test_the_committed_table_has_no_model_text_fields_and_only_integer_answers(release):
    table = st.read(ROOT / "eval" / "scores" / release)
    assert table
    for run in table:
        for it in run["items"]:
            assert set(it) == set(st.ITEM_FIELDS)
            assert it["parsed_answer"] is None or type(it["parsed_answer"]) is int
            assert type(it["gold"]) is int and type(it["correct"]) is bool
