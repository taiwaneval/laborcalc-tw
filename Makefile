FAMILY  ?= overtime_pay
# 預設 release：FAMILY 所屬資料集在 datasets.yaml 的最新 release；要指定就 make … RELEASE=2026Q4
RELEASE ?= $(shell python3 tools/dataset_registry.py latest-release --family $(FAMILY))
# provider 用 openai-api/<service>/<model>；端點與 key 走環境變數 <SERVICE>_BASE_URL / <SERVICE>_API_KEY
# （例：MODEL=openai-api/omlx/gemma-4-e4b-it-OptiQ-4bit，export OMLX_BASE_URL=http://127.0.0.1:5566/v1 OMLX_API_KEY=...）
MODEL   ?= openai-api/vllm/qwen3-8b
TARGET  ?= unspecified@unspecified@unspecified@$(ANSWER_MODE)
# answer_mode：direct（只回整數）或 cot（列計算過程，最後一行「答案：<整數>」）；target 命名慣例加 @direct／@cot 後綴
ANSWER_MODE ?= direct
# thinking：disable（請求帶 chat_template_kwargs.enable_thinking=false，oMLX/vLLM）或 default（不加；OpenAI 官方 API 用這個）
THINKING    ?= disable
# cot 要給推理空間；direct 留空即用 provider 預設
MAX_TOKENS  ?= $(if $(filter cot,$(ANSWER_MODE)),1024,)
TEMPERATURE ?= 0
MAX_CONN    ?= 1
INSPECT     ?= inspect
# aggregate 模式：preview（預設，照寫 index 並列出 release 阻擋項）或 release（有阻擋項就失敗、不寫檔）
MODE        ?= preview
# reproduce 用來建暫存 venv 的 Python（requirements.txt 以 3.12 驗證）；ONLINE=1 另向鏡像 commit 核對法條原檔
REPRO_PYTHON ?= python3.12

test:
	python3 silver/families/$(FAMILY)/test_agreement.py

gold:
	python3 gold/generate.py $(FAMILY) --release $(RELEASE) --split public --n 200

eval:
	$(INSPECT) eval eval/tasks/rules_registry.py@rules_registry \
	  -T release=$(RELEASE) -T split=public -T family=$(FAMILY) -T evaluation_target=$(TARGET) \
	  -T answer_mode=$(ANSWER_MODE) -T thinking=$(THINKING) \
	  --model $(MODEL) --temperature $(TEMPERATURE) --max-connections $(MAX_CONN) \
	  $(if $(MAX_TOKENS),--max-tokens $(MAX_TOKENS),) --log-dir eval/logs/$(RELEASE)

# 逐題得分表 eval/scores/<release>/：由 eval/logs 的原始紀錄產生（沒有作答原文）。新增或更換紀錄後先跑這個，再跑 aggregate
scores:
	python3 eval/score_table.py --release $(RELEASE)

aggregate:
	python3 eval/aggregate.py --release $(RELEASE) --mode $(MODE)

# 從乾淨 clone 逐層重現 bronze → silver → gold → index 並跑測試；只用 requirements.txt 安裝（tools/reproduce.py）
reproduce:
	$(REPRO_PYTHON) tools/reproduce.py $(if $(filter 1 true yes,$(ONLINE)),--online,)

site:
	python3 tools/build_page.py silver/families/$(FAMILY)
	python3 tools/coverage_report.py N0030001
	python3 tools/build_index_page.py $(RELEASE)
	python3 tools/build_arch.py

.PHONY: test gold eval scores aggregate reproduce site
