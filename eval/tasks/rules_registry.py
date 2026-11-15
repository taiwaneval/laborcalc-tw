"""Inspect AI task：把 gold/releases/<release>/<split>/<family>.jsonl 當 dataset 跑任一模型。

每個 sample 的 metadata 帶完整 lineage（family 版本、spec/rule/bronze hash、triage 版本、seed、branch、knobs），
所以 Inspect 的 .eval log 本身就是可重現的證據：從 log 裡任一題可以追到法條快照。

用法：
  inspect eval eval/tasks/rules_registry.py@rules_registry \
      -T release=2026Q4 -T split=public -T family=overtime_pay \
      --model mockllm/model --log-dir eval/logs/2026Q4
  （-T release 省略時用 family 所屬資料集在 datasets.yaml 的最新 release）

  # 本機 vLLM / Ollama / oMLX（OpenAI 相容端點）：用 openai-api/<service>/ provider。
  # （openai/ provider 會把不認得的模型名當 GPT 前沿模型，悄悄丟掉 temperature。）
  OMLX_BASE_URL=http://127.0.0.1:5566/v1 OMLX_API_KEY=... \
  inspect eval ... --model openai-api/omlx/gemma-4-e4b-it-OptiQ-4bit --temperature 0 \
      -T evaluation_target=gemma-4-e4b-it@optiq4-mlx@mac-studio-applem1ultra@direct -T answer_mode=direct

evaluation_target 是 TCRI v0.1 第 8 點要求的「exact served model + 量化 + 硬體」識別，
寫進 log 的 task_args，aggregate.py 以它為排名單位，不用 model 名稱。

answer_mode（Patch B，2026-10-08）是評測層開關，也是 target identity 的一部分：
  direct：題幹原樣（family templates.yaml 的 answer_instruction：只回一個整數）。
  cot   ：把題幹結尾的 answer_instruction 整段換成該 family 的 cot_instruction（templates.yaml；未設則用 COT_INSTRUCTION：
          可列計算過程，最後一行「答案：<整數>」）。題目對取整有要求的 family（加班費：無條件進位）要在 cot_instruction 寫明。
          scorer 不變（抽回覆中最後一個整數）。跑 cot 請加 --max-tokens 1024。
evaluation_target 命名慣例加後綴 @direct／@cot；aggregate 以 target 字串分組，所以兩種模式不會合併。

thinking（2026-10-09）：推理模型在 direct 模式會自己先想（gpt-oss-20b 平均每題 3,600 tokens、Qwen3.8-27B 一題 31k），
  direct 就不再是 direct。預設 thinking=disable：在請求 body 加 chat_template_kwargs.enable_thinking=false
  （oMLX／vLLM 都吃這個欄位；Qwen3、gpt-oss 的 chat template 會關掉 thinking）。
  thinking=disable_dashscope：百鍊 compatible-mode 的頂層 enable_thinking=false；thinking=disable_openrouter：OpenRouter 的 reasoning.enabled=false。
  thinking=default 不加任何東西（OpenAI 官方 API 不認識這些欄位會 400，用 --reasoning-effort 控制）。
  thinking 寫進 task metadata。
  開推理（2026-11-10）：max_openrouter（reasoning.effort=max）、enable_dashscope、enable（chat_template_kwargs）。
  release 評測一律開最高推理；百鍊 disable_dashscope 的舊 run 當「沒有推理」的對照。
  已知例外：gpt-oss-20b（Harmony 模板）不理 enable_thinking，仍每題推理 28k–32k tokens；它只認 reasoning_effort，待處理。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Scorer, Target, accuracy, scorer, stderr
from inspect_ai.solver import TaskState, generate, solver

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry  # noqa: E402

RELEASES = ROOT / "gold" / "releases"
FAMILIES = ROOT / "silver" / "families"

ANSWER_MODES = ("direct", "cot")
# thinking → 請求 body 要加的欄位。不用 off/no 當值：Inspect 會把 -T thinking=off 解析成布林 False。
THINKING_BODIES: dict[str, dict] = {
    "disable": {"chat_template_kwargs": {"enable_thinking": False}},   # oMLX／vLLM（Qwen3、Gemma 等 chat template）
    "disable_dashscope": {"enable_thinking": False},                    # 阿里百鍊 compatible-mode（頂層欄位）
    "disable_openrouter": {"reasoning": {"enabled": False}},            # OpenRouter 統一 reasoning 參數
    "default": {},                                                      # 不加（OpenAI 官方 API；用 --reasoning-effort 控制）
    # 最高推理（release 評測一律開到最高；2026-11-10 逐一探測過可用）
    "max_openrouter": {"reasoning": {"effort": "max"}},                 # OpenRouter：luna、haiku、gemini flash、glm flash 都接受 max
    "enable_dashscope": {"enable_thinking": True},                      # 百鍊：不給 thinking_budget 即用模型上限
    "enable": {"chat_template_kwargs": {"enable_thinking": True}},      # oMLX／vLLM 本機模型
    # gpt-oss 最高 effort 是 high；鎖在原生 MXFP4（fp4）且輸出上限夠長的供應商，不退回 fp8/bf16 或 32k 上限的家
    "high_openrouter_mxfp4": {"reasoning": {"effort": "high"},
                              "provider": {"order": ["CoreWeave", "Parasail", "AkashML"], "quantizations": ["fp4"],
                                           "allow_fallbacks": False}},
}
THINKING_MODES = tuple(THINKING_BODIES)
COT_INSTRUCTION = "可以列出計算過程，最後一行請以「答案：<整數>」作結，整數不含單位。"

# 題幹已要求「只回答一個整數」。scorer 仍寬鬆抽數字：
# 1) 回覆裡有「答案：<整數>」（cot 模式要求的結尾格式）就取最後一個這樣的整數；
# 2) 否則取回覆中最後一個整數（容忍「答：1,900 元」）。
# 先看 1) 是因為 cot 回覆常在答案後再補一句「（依勞基法第 24 條）」，只抓最後整數會抓到 24。direct 回覆沒有「答案：」前綴，行為不變。
INT_RE = re.compile(r"-?\d[\d,]*")
ANSWER_LINE_RE = re.compile(r"答案\s*[:：]\s*\**\s*(-?\d[\d,]*)")


def extract_integer(text: str) -> int | None:
    tagged = ANSWER_LINE_RE.findall(text)
    if tagged:
        return int(tagged[-1].replace(",", ""))
    nums = INT_RE.findall(text)
    return int(nums[-1].replace(",", "")) if nums else None


# 從題目帶進 sample metadata 的欄位（lineage 全部保留，log 才能追到法條快照）
METADATA_KEYS = (
    "family", "family_version", "family_status", "spec_sha256", "rule_sha256", "templates_sha256", "sampler_sha256",
    "generator_version",
    "release", "split", "seed", "rate_table", "lineage", "as_of", "branch", "pair_id", "params", "knobs",
    "answer_type", "tolerance", "tier",
)


def family_answer_instruction(family: str) -> str:
    """該 family templates.yaml 的 answer_instruction（題幹結尾那句「只回答一個整數」）。"""
    import yaml
    return yaml.safe_load((FAMILIES / family / "templates.yaml").read_text())["answer_instruction"].strip()


def family_cot_instruction(family: str) -> str:
    """cot 模式換上的結尾說明：templates.yaml 的 cot_instruction，未設則用 COT_INSTRUCTION。"""
    import yaml
    return (yaml.safe_load((FAMILIES / family / "templates.yaml").read_text()).get("cot_instruction") or COT_INSTRUCTION).strip()


def rewrite_prompt(prompt: str, answer_mode: str, instruction: str, cot_instruction: str = COT_INSTRUCTION) -> str:
    """cot：題幹結尾的 answer_instruction 整段字串比對後換成 cot_instruction；比對不到就 fail loud，
    不要默默把 direct 題當 cot 題跑。direct：原樣。"""
    if answer_mode == "direct":
        return prompt
    body = prompt.rstrip()
    if not body.endswith(instruction):
        raise ValueError(f"prompt 結尾不是 templates.yaml 的 answer_instruction，無法改寫成 cot：{prompt[-60:]!r}")
    return body[: -len(instruction)] + cot_instruction


def load_samples(release: str, split: str, family: str, answer_mode: str = "direct") -> list[Sample]:
    if answer_mode not in ANSWER_MODES:
        raise ValueError(f"answer_mode 必須是 {ANSWER_MODES}，得到 {answer_mode!r}")
    path = RELEASES / release / split / f"{family}.jsonl"
    instruction, cot_instruction = family_answer_instruction(family), family_cot_instruction(family)
    samples = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        samples.append(Sample(
            id=it["item_id"],
            input=rewrite_prompt(it["prompt"], answer_mode, instruction, cot_instruction),
            target=str(it["gold"]),
            metadata={k: it[k] for k in METADATA_KEYS if k in it},   # rate_table 只有部分 family 有
        ))
    return samples


@scorer(metrics=[accuracy(), stderr()])
def integer_match() -> Scorer:
    """answer_type=integer_twd：抽回覆中最後一個整數，與 gold 比對（tolerance 來自 item）。
    回覆裡沒有任何整數 → INCORRECT（這是模型失敗，留在分母；evaluator 失敗由 Inspect 另記為 error）。"""
    async def score(state: TaskState, target: Target) -> Score:
        text = state.output.completion or ""
        got = extract_integer(text)
        if got is None:
            return Score(value=INCORRECT, answer=None, explanation="no integer in completion")
        gold = int(target.text)
        tol = int(state.metadata.get("tolerance", 0))
        ok = abs(got - gold) <= tol
        return Score(value=CORRECT if ok else INCORRECT, answer=str(got),
                     explanation=f"got={got} gold={gold} tol={tol}")
    return score


@solver
def oracle_solver():
    """作弊 solver：直接回 gold。只用來驗證 scorer 與 pipeline，不是評測。"""
    async def solve(state: TaskState, generate_fn):
        state.output.completion = state.target.text
        return state
    return solve


def _family_hypotheses(family: str):
    """silver/families/<family>/hypotheses.py（以唯一模組名載入，快取）。"""
    if family not in _HYPOTHESES:
        import importlib.util
        s = importlib.util.spec_from_file_location(f"{family}_hypotheses", FAMILIES / family / "hypotheses.py")
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        _HYPOTHESES[family] = m
    return _HYPOTHESES[family]


_HYPOTHESES: dict[str, object] = {}


@solver
def naive_rule_solver():
    """各 family 自帶的弱模型（silver/families/<id>/hypotheses.py:naive_answer）：
    overtime_pay 只記得 4/3、忘了 5/3 與國定假日規則；annual_leave_days 用 (Q − H) ÷ 365 算年資、忽略民法 §121。
    從 metadata.params 讀參數（真模型沒有這個通道）。只用來讓 pipeline 出現部分錯誤，不是評測。"""
    async def solve(state: TaskState, generate_fn):
        ans = _family_hypotheses(state.metadata["family"]).naive_answer(state.metadata["params"])
        state.output.completion = f"依規定應為 {ans:,}。"
        return state
    return solve


@solver
def random_solver():
    """以 seed 決定的亂數整數；驗證 scorer 對完全無關回覆的處理與 chance level。"""
    import random
    async def solve(state: TaskState, generate_fn):
        rng = random.Random(state.metadata["seed"])
        state.output.completion = str(rng.randint(0, 5000))
        return state
    return solve


FAKE_SOLVERS = {"oracle": oracle_solver, "naive": naive_rule_solver, "random": random_solver}


def default_release(family: str) -> str:
    """沒指定 release 時：family 所屬資料集（datasets.yaml）的最新 release。"""
    ds = dataset_registry.load().dataset_of_family(family)
    if ds is None or not ds.latest_release:
        raise ValueError(f"family {family} 沒有登記在 datasets.yaml 的資料集，或該資料集還沒有 release；請指定 -T release=")
    return ds.latest_release


@task
def rules_registry(release: str = "", split: str = "public", family: str = "overtime_pay",
                   evaluation_target: str = "unspecified", fake: str = "", answer_mode: str = "direct",
                   thinking: str = "disable") -> Task:
    """fake ∈ {oracle, naive, random} 時不呼叫模型，只驗 pipeline；正式評測留空並以 --model 指定。
    release 留空 = family 所屬資料集的最新 release（datasets.yaml）。
    answer_mode ∈ {direct, cot}、thinking ∈ THINKING_MODES（見檔頭）；都寫進 task metadata，aggregate 會帶進 index。"""
    if thinking not in THINKING_MODES:
        raise ValueError(f"thinking 必須是 {THINKING_MODES}，得到 {thinking!r}")
    release = release or default_release(family)
    return Task(
        dataset=load_samples(release, split, family, answer_mode),
        solver=FAKE_SOLVERS[fake]() if fake else generate(),
        scorer=integer_match(),
        config=GenerateConfig(extra_body=THINKING_BODIES[thinking]) if THINKING_BODIES[thinking] else GenerateConfig(),
        metadata={"evaluation_target": evaluation_target, "release": release, "split": split,
                  "family": family, "fake": fake or None, "answer_mode": answer_mode, "thinking": thinking},
        name=f"rules_registry/{family}/{release}/{split}",
    )
