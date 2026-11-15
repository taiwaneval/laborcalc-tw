"""已提交的 public 題集必須能用目前的程式逐 byte 重生。

程式改動而改變了題目時這裡會失敗：重生題集並 commit。改動來自共用產生器（gold/generate.py、tools/numerals.py、
tools/sampling.py）時還要 bump GENERATOR_VERSION，否則舊 log 不會被 aggregate 標成過期；generate 的版本守門會擋住沒升版的重生。

重生時以 manifest 記的 generator_version 當版本標籤（--pin-generator-version）：bump 只改標籤、不改題目時，
已出貨的 release 不必重生、作答紀錄也不會過期；題目真的變了，這裡照樣失敗。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dataset_registry  # noqa: E402

# 每個資料集（datasets.yaml）最新 release 的每個 public 題集；舊 release 在它的 release tag 上檢查
SETS = [(rel, mf.name.removesuffix(".manifest.json")) for _, rel in dataset_registry.load().latest_releases()
        for mf in sorted((ROOT / "gold/releases" / rel / "public").glob("*.manifest.json"))]


def load_generate():
    s = importlib.util.spec_from_file_location("gen_identical", ROOT / "gold/generate.py")
    gen = importlib.util.module_from_spec(s); s.loader.exec_module(gen); return gen


def test_every_latest_release_has_public_sets():
    assert SETS and {r for r, _ in SETS} == {r for _, r in dataset_registry.load().latest_releases()}


@pytest.mark.parametrize("release, fid", SETS)
def test_committed_public_set_regenerates_byte_identical(tmp_path, release, fid):
    REL = ROOT / "gold/releases" / release / "public"
    m = json.loads((REL / f"{fid}.manifest.json").read_text())
    # 已出貨的 release 釘住自己的產生器版本：以 manifest 記的版本標籤重生，其餘內容要逐 byte 相同
    load_generate().generate(fid, release, "public", m["n"] + m["rejected"], out_dir=tmp_path, as_of=m["as_of"],
                             pin_generator_version=m["generator_version"])
    for ext in ("jsonl", "coverage.json", "manifest.json"):
        assert (tmp_path / f"{fid}.{ext}").read_bytes() == (REL / f"{fid}.{ext}").read_bytes(), \
            f"{fid}.{ext} 與已提交的不同：重生題集；若改動來自共用產生器，先 bump GENERATOR_VERSION"
