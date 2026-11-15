"""silver → gold：從 family spec 生成帶 lineage 的題目（JSONL）。

每題的 seed 由 (salt, release, family, idx) 派生：
  public  split 用 repo 內的固定 salt，任何人可重現
  private split 用環境變數 TAIWANEVAL_PRIVATE_SALT，不進 repo；下一版 release 時公開該 salt 即轉為 public
同一參數空間、不同 salt → 不同題；公開題外流也對不上私有題。

0.2.0 起本檔是通用框架：抽樣、模板欄位與 gate 字串由各 family 的 silver/families/<id>/sampler.py 提供
（契約見 silver/families/overtime_pay/sampler.py 檔頭；plan() 的項目可帶 knobs，會併入題目 knobs 與 coverage）。本檔只做 seed 派生、lineage、呼叫 rule.compute、
gate、寫檔。每題 RNG 消耗順序固定：sampler.sample() → 模板 → 姓 → 名 → sampler.context()。

產物：
  gold/releases/<release>/<split>/<family>.jsonl   題目
  gold/releases/<release>/<split>/<family>.coverage.json   分支與旋鈕直方圖
  gold/releases/<release>/<split>/<family>.manifest.json   lineage 摘要（spec hash、bronze hash、triage 版本、生成器版本）

用法：
  python3 gold/generate.py overtime_pay --release 2026Q4 --split public --n 200
  python3 gold/generate.py overtime_pay --release 2026Q4 --split public --n 200 --as-of 2026-10-08   # 重生既有 release 時沿用原 as_of
  python3 gold/generate.py overtime_pay --release 2026Q4 --n 200 --as-of 2026-10-08 --out-dir /tmp/x --pin-generator-version 0.5.0
                                                     # 已出貨的 release 以它 manifest 記的版本重生比對
  TAIWANEVAL_PRIVATE_SALT=... python3 gold/generate.py overtime_pay --release 2026Q4 --split private --n 200
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from paths import bronze_dir, family_dir, inventory_dir, INVENTORY, RELEASES  # noqa: E402
from family_reviews import HASH_FIELDS, family_hashes, status_for  # noqa: E402
import fetch_source  # noqa: E402  — bronze 快照的共用判讀（source_type、check_snapshot）

GENERATOR_VERSION = "0.6.0"   # 0.6.0：lineage 讀 spec 的 bronze_sources，寫入非法規來源（lineage.sources）與只收快照、沒有 inventory 的法規（併入 lineage.laws）的 bronze sha256；沒有 bronze_sources 的 family 只有本欄位改變，題目不變。已出貨的 release 以 manifest 記的版本重生比對（--pin-generator-version；Calvin 2026-11-13 裁定）。0.5.0：參數去重、骨架近似重複重抽、配對題 pair_id、模板在分支內平均、manifest 加 diversity。0.4.0：題目的 rate_table 改名 minimum_wage（最低工資法）。0.3.0：lineage 加 templates_sha256、sampler_sha256，題幹不變。0.2.0：抽樣與渲染搬進各 family 的 sampler.py
PUBLIC_SALT = "taiwaneval-public-2026"
DEFAULT_ANSWER = {"type": "integer_twd", "tolerance": 0}   # spec 沒有 answer 區塊時（overtime_pay 0.1.x）


# --------------------------------------------------------------------------- lineage
def _load_module(name: str, path: Path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def load_family(fid: str):
    fdir = family_dir(fid)
    spec_path = fdir / "spec.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    tpl = yaml.safe_load((fdir / "templates.yaml").read_text())
    rule = _load_module(f"rule_{fid}", fdir / spec["implementation"]["python"])
    sampler = _load_module(f"sampler_{fid}", fdir / "sampler.py")
    # spec／rule／templates／sampler 的 sha256：題目的 lineage，也是 family 簽核的對象（tools/family_reviews.py）
    return spec, tpl, rule, sampler, family_hashes(fdir)


# 非法規來源寫進 lineage 的欄位，另加 bronze_sha256（provenance 的 sha256，與法規同名）與 fetched_at
SOURCE_LINEAGE = ("source_type", "title", "publisher", "published_on", "document_number", "version")


def law_entry(prov: dict) -> dict:
    """lineage.laws 的共同欄位。經 inventory 連到的法規另加 triage_version 與 units；只收快照的法規沒有這兩項。"""
    return {"law_name": prov["law_name"], "last_amended": prov["last_amended"],
            "bronze_sha256": prov["sha256"], "mirror_commit": prov.get("mirror_commit")}


def bronze_source_lineage(fid: str, source_ids: list[str]) -> tuple[dict, dict]:
    """spec 的 bronze_sources → (laws, sources)。快照先經 fetch_source.check_snapshot() 核對，
    缺檔、被手改、欄位不齊都拒絕出題。
      法規（source_type 是 law，或沒有這個欄位的舊快照）：只收 bronze、沒有 silver/inventory/<pcode>/ 的才能列在這裡，
        寫進 lineage.laws，形狀與 inventory 連到的法規相同，但沒有 triage_version 與 units。有 inventory 的法規要經
        families.yaml 登記，否則同一部法規會在 lineage 出現兩次、條項也對不上。
      非法規來源：寫進 lineage.sources（來源類型、名稱、版本識別、bronze_sha256、fetched_at）。"""
    laws, sources = {}, {}
    for sid in source_ids:
        d = bronze_dir(sid)
        if not (d / "provenance.json").exists():
            raise SystemExit(f"{fid}：spec 的 bronze_sources 列了 {sid}，但 bronze/{sid}/provenance.json 不存在"
                             "（法規用 tools/fetch_law.py、非法規來源用 tools/fetch_source.py 收進 bronze）")
        prov = json.loads((d / "provenance.json").read_text(encoding="utf-8"))
        is_law = fetch_source.source_type(prov) == "law"
        if is_law and inventory_dir(sid).exists():
            raise SystemExit(f"{fid}：bronze/{sid} 有 silver/inventory/{sid}/；有盤點的法規要在 silver/inventory/{sid}/families.yaml "
                             "登記 family 與條項，不列在 bronze_sources")
        problems = fetch_source.check_snapshot(d)
        if problems:
            raise SystemExit(f"{fid}：bronze/{sid} 核對失敗：{'；'.join(problems)}")
        if is_law:
            laws[sid] = law_entry(prov)
        else:
            sources[sid] = {**{k: prov[k] for k in SOURCE_LINEAGE}, "bronze_sha256": prov["sha256"], "fetched_at": prov["fetched_at"]}
    return laws, sources


def lineage_for(fid: str, spec: dict | None = None) -> dict:
    """family → 引用的法規 → bronze hash、triage 版本；spec 有 bronze_sources 時，只收快照的法規併入 laws（依 pcode 排序），
    非法規來源另放 sources。沒有 bronze_sources 的 family 輸出與 0.5.0 相同：既有題集的 lineage 一個字都不變。"""
    out = {"laws": {}}
    for pcode_dir in sorted(p for p in INVENTORY.iterdir() if p.is_dir()):
        fam_map = pcode_dir / "families.yaml"
        if not fam_map.exists():
            continue
        fams = yaml.safe_load(fam_map.read_text())["families"]
        if any(f["id"] == fid for f in fams):
            pcode = pcode_dir.name
            prov = json.loads((bronze_dir(pcode) / "provenance.json").read_text())
            triage = yaml.safe_load((inventory_dir(pcode) / "triage.yaml").read_text())
            out["laws"][pcode] = law_entry(prov) | {
                "triage_version": triage["triage_version"],
                "units": [u["unit_id"] for f in fams if f["id"] == fid for u in f["units"]],
            }
    if spec and spec.get("bronze_sources"):
        laws, sources = bronze_source_lineage(fid, list(spec["bronze_sources"]))
        out["laws"] = dict(sorted((out["laws"] | laws).items()))
        if sources:
            out["sources"] = sources
    return out


# --------------------------------------------------------------------------- sampling
def derive_seed(salt: str, release: str, fid: str, idx: int) -> str:
    return hashlib.sha256(f"{salt}|{release}|{fid}|{idx}".encode()).hexdigest()


# --------------------------------------------------------------------------- rendering
def render(tpl: dict, rng: random.Random, planned: dict, params: dict, sampler,
           used: Counter | None = None, exclude: set[str] = frozenset()) -> tuple[str, dict, dict]:
    """(prompt, knobs, fields)。RNG 順序固定：模板 → 姓 → 名 → sampler.context()。
    模板在同一分支內平均：只從該分支用得最少的模板裡抽；exclude 是同一配對已用過的模板。"""
    pool = [t for t in tpl[planned.get("template_pool", "templates")] if t["id"] not in exclude]
    if used is not None:
        low = min(used[t["id"]] for t in pool)
        pool = [t for t in pool if used[t["id"]] == low]
    t = rng.choice(pool)
    name = rng.choice(tpl["names"]["surnames"]) + rng.choice(tpl["names"]["given"])
    fields, extra = sampler.context(rng, params, tpl)
    prompt = t["text"].format(name=name, **fields) + "\n\n" + tpl["answer_instruction"]
    knobs = {"template": t["id"], "register": t["register"], "implicit": bool(t.get("implicit")), **extra,
             **planned.get("knobs", {}), "name": name}
    return prompt, knobs, fields


# --------------------------------------------------------------------------- diversity
MAX_TEMPLATE_SHARE = 0.15     # 單一模板占全部題目的上限（manifest diversity 與測試把關）
MAX_PARAM_RETRIES = 50        # 參數與已出過的題重複時，換 seed 重抽的次數上限
MAX_SKELETON_RETRIES = 12     # 同分支骨架已用過時，重新渲染的次數上限
MAX_SKELETON_REUSE = 2        # 同分支同一骨架最多出現幾次（數字遮掉後句型可選的組合有限，完全不重用做不到）
_NUM = re.compile(r"[0-9０-９零〇一二兩三四五六七八九十百千萬][0-9０-９零〇一二兩三四五六七八九十百千萬,.]*")


def skeleton(prompt: str, name: str) -> str:
    """遮掉姓名與所有數字（阿拉伯、中文、萬／千混寫）後的句子骨架；同分支骨架相同視為近似重複。"""
    return _NUM.sub("#", prompt.replace(name, "◯"))


def plan_pairs(plan: list[dict], pairing: dict | None, rng: random.Random) -> dict[int, int]:
    """{partner idx: anchor idx}。配對只在同一分支的一般題之間（對照題只有一個模板，不配）；
    partner 沿用 anchor 的參數、換模板出題，所以分支題數不變。pairing = {rate: 配對題占一般題的比例, size: 每組題數}。"""
    if not pairing:
        return {}
    size = int(pairing.get("size", 2))
    eligible = [i for i, p in enumerate(plan) if p.get("template_pool", "templates") == "templates"]
    n_groups = round(len(eligible) * float(pairing["rate"]) / size)
    order = eligible[:]
    rng.shuffle(order)
    free = set(eligible)
    partner_of: dict[int, int] = {}
    for a in order:
        if n_groups == 0:
            break
        if a not in free:
            continue
        mates = [i for i in order if i in free and i != a and plan[i]["branch"] == plan[a]["branch"]][: size - 1]
        if len(mates) < size - 1:
            continue
        free.discard(a)
        for m in mates:
            free.discard(m); partner_of[m] = a
        n_groups -= 1
    return partner_of


# --------------------------------------------------------------------------- gates
def gate(prompt: str, visible_sets: list[set[str]]) -> list[str]:
    """確定性模板的最小檢查：每個 visible 集合至少命中一個（題幹找得回參數），且無漏填的佔位符。"""
    errs = [f"missing:{sorted(s)[0]}" for s in visible_sets if not any(f in prompt for f in s)]
    if "{" in prompt or "}" in prompt:
        errs.append("unfilled_placeholder")
    return errs


# --------------------------------------------------------------------------- version guard
# 決定一題「問什麼、答什麼」的欄位。family_status 與 lineage 不在內：簽核後重生、法規快照更新都會合法地改到它們。
QUESTION_KEYS = ("item_id", "seed", "branch", "pair_id", "params", "knobs", "prompt", "gold", "gold_exact", "answer_type", "tolerance", "tier")
VERSION_KEYS = ("family_version", *HASH_FIELDS, "generator_version", "release", "split", "salt", "as_of")


def same_version_different_questions(out_dir: Path, fid: str, manifest: dict, items: list[dict]) -> bool:
    """既有題集與新題集版本欄位全同，題目卻不同 → True。那只可能是共用產生器改了輸出卻沒 bump GENERATOR_VERSION。"""
    old_m, old_j = out_dir / f"{fid}.manifest.json", out_dir / f"{fid}.jsonl"
    if not (old_m.exists() and old_j.exists()):
        return False
    om = json.loads(old_m.read_text())
    if any(om.get(k) != manifest.get(k) for k in VERSION_KEYS):
        return False
    old = [{k: it.get(k) for k in QUESTION_KEYS} for it in (json.loads(l) for l in old_j.read_text().splitlines() if l.strip())]
    return old != [{k: it.get(k) for k in QUESTION_KEYS} for it in items]


# --------------------------------------------------------------------------- main
def generate(fid: str, release: str, split: str, n: int, out_dir: Path | None = None, as_of: str | None = None,
             pin_generator_version: str | None = None) -> None:
    """pin_generator_version：以這個版本標籤取代 GENERATOR_VERSION，只供重生比對。
    已出貨的 release 釘住自己的產生器版本：用它 manifest 記的版本重生，其餘內容要與已提交的逐 byte 相同，
    證明現行程式產生的題目和已出貨的一樣。只能寫到 --out-dir 指定的暫存目錄，不能寫進 gold/releases/。"""
    if pin_generator_version is not None:
        if out_dir is None:
            raise SystemExit("--pin-generator-version 只能搭配 --out-dir 使用（重生比對用，不寫進 gold/releases/）。未寫入任何檔案。")
        if Path(out_dir).resolve().is_relative_to(RELEASES.resolve()):
            raise SystemExit("--pin-generator-version 不能寫進 gold/releases/：舊版本標籤只用來比對，不能貼在新產生的題集上。未寫入任何檔案。")
    gv = pin_generator_version or GENERATOR_VERSION
    salt = PUBLIC_SALT if split == "public" else os.environ.get("TAIWANEVAL_PRIVATE_SALT")
    if not salt:
        raise SystemExit("private split 需要環境變數 TAIWANEVAL_PRIVATE_SALT")
    spec, tpl, rule, sampler, hashes = load_family(fid)
    # family 狀態只由 silver/families/<id>/reviews.yaml 推得
    family_status = status_for(family_dir(fid), hashes)["status"]
    lineage = lineage_for(fid, spec)
    answer = spec.get("answer", DEFAULT_ANSWER)
    rt = spec.get("rate_tables", {}).get("minimum_wage")
    as_of = as_of or date.today().isoformat()

    master = random.Random(derive_seed(salt, release, fid, -1))
    plan = sampler.plan(spec, n, master)

    out_dir = Path(out_dir) if out_dir else RELEASES / release / split
    out_dir.mkdir(parents=True, exist_ok=True)
    items, rejected = [], []
    cov: dict[str, Counter] = {"branch": Counter()}

    partner_of = plan_pairs(plan, tpl.get("pairing"), random.Random(derive_seed(salt, release, fid, -2)))
    anchors = set(partner_of.values())
    pair_id = {i: f"{fid}/{release}/{split}/p{a:04d}" for i, a in partner_of.items()} | {a: f"{fid}/{release}/{split}/p{a:04d}" for a in anchors}

    # 第一輪：抽參數。非 partner 的題參數不得與已出過的題重複，重複就換 seed 重抽。
    sampled: dict[int, tuple[str, random.Random, dict]] = {}
    seen_params: set[str] = set()
    param_duplicates = 0
    for idx, p in enumerate(plan):
        if idx in partner_of:
            continue
        for attempt in range(MAX_PARAM_RETRIES + 1):
            seed = derive_seed(salt if attempt == 0 else f"{salt}#retry{attempt}", release, fid, idx)
            rng = random.Random(seed)
            params = sampler.sample(rng, p)
            key = json.dumps(params, sort_keys=True)
            if key not in seen_params:
                break
        else:
            param_duplicates += 1
        seen_params.add(key)
        sampled[idx] = (seed, rng, params)
    for idx, a in partner_of.items():
        seed = derive_seed(salt, release, fid, idx)
        sampled[idx] = (seed, random.Random(seed), sampled[a][2])

    # 第二輪：依序渲染。模板在分支內平均；同組配對不重用模板；同分支骨架重複就重新渲染。
    used: dict[str, Counter] = defaultdict(Counter)
    pair_templates: dict[str, set[str]] = defaultdict(set)
    seen_skel: Counter = Counter()
    near_duplicates = 0
    for idx, p in enumerate(plan):
        seed, rng, params = sampled[idx]
        pid = pair_id.get(idx)
        res = rule.compute(**params)
        best = None
        for attempt in range(MAX_SKELETON_RETRIES + 1):
            prompt, knobs, fields = render(tpl, rng, p, params, sampler, used[p["branch"]], pair_templates[pid] if pid else frozenset())
            sk = (p["branch"], skeleton(prompt, knobs["name"]))
            if pid or seen_skel[sk] == 0:
                break
            best = best if best and seen_skel[best[3]] <= seen_skel[sk] else (prompt, knobs, fields, sk)
        else:
            prompt, knobs, fields, sk = best          # 每次都撞到用過的骨架：取重用次數最少的那次
        if not pid:
            near_duplicates += seen_skel[sk] > 0
            seen_skel[sk] += 1
        used[p["branch"]][knobs["template"]] += 1
        if pid:
            pair_templates[pid].add(knobs["template"])
        errs = gate(prompt, sampler.visible(params, fields))
        item = {
            "item_id": f"{fid}/{release}/{split}/{idx:04d}",
            "family": fid, "family_version": spec["version"], "family_status": family_status,
            **hashes, "generator_version": gv,
            "release": release, "split": split, "seed": seed,
        }
        if rt:
            item["rate_table"] = {"id": "minimum_wage", "effective_from": rt["effective_from"], "monthly": rt["monthly"]}
        item.update({
            "lineage": lineage, "as_of": as_of,
            "branch": p["branch"], "pair_id": pid, "params": params, "knobs": knobs,
            "prompt": prompt,
            "gold": res["total"], "gold_exact": str(res["total_exact"]),
            "answer_type": answer["type"], "tolerance": answer["tolerance"],
            "tier": spec["tier"],
        })
        if errs:
            rejected.append({"item_id": item["item_id"], "errors": errs}); continue
        items.append(item)
        cov["branch"][p["branch"]] += 1
        for k, v in knobs.items():
            if k != "name":
                cov.setdefault(k, Counter())[str(v)] += 1
        for k, v in (sampler.coverage_keys(params) if hasattr(sampler, "coverage_keys") else {}).items():
            cov.setdefault(k, Counter())[str(v)] += 1

    tcount = Counter(it["knobs"]["template"] for it in items)
    diversity = {
        "template_counts": dict(sorted(tcount.items())),
        "max_template_share": max(tcount.values()) / len(items) if items else 0.0,
        "max_template_share_cap": MAX_TEMPLATE_SHARE,
        "skeletons": len({(it["branch"], skeleton(it["prompt"], it["knobs"]["name"])) for it in items}),
        "near_duplicates": near_duplicates,
        "max_skeleton_reuse": max(seen_skel.values(), default=0),
        "param_duplicates": param_duplicates,
        "pairs": len({it["pair_id"] for it in items if it["pair_id"]}),
        "paired_items": sum(1 for it in items if it["pair_id"]),
    }

    manifest = {
        "family": fid, "family_version": spec["version"], "family_status": family_status,
        **hashes, "generator_version": gv,
        "release": release, "split": split, "salt": salt if split == "public" else "<withheld>",
        "n": len(items), "rejected": len(rejected), "lineage": lineage, "as_of": as_of,
        "diversity": diversity,
    }
    if same_version_different_questions(out_dir, fid, manifest, items):
        raise SystemExit(
            f"{fid}：題目變了，但 family 檔（spec／rule／templates／sampler）與 GENERATOR_VERSION {GENERATOR_VERSION} 都沒變。"
            "改動來自共用產生器（gold/generate.py、tools/numerals.py、tools/sampling.py）時要 bump GENERATOR_VERSION，"
            "否則舊 log 的版本欄位和新題集一樣，aggregate 不會把它們標成過期。未寫入任何檔案。")
    (out_dir / f"{fid}.jsonl").write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n")
    (out_dir / f"{fid}.coverage.json").write_text(json.dumps({k: dict(v) for k, v in cov.items()} | {"n": len(items), "rejected": rejected}, ensure_ascii=False, indent=1))
    (out_dir / f"{fid}.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(json.dumps({"n": len(items), "rejected": len(rejected), "branch": dict(cov["branch"])}, ensure_ascii=False))
    print(f"→ {out_dir / (fid + '.jsonl')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("--release", required=True)
    ap.add_argument("--split", choices=["public", "private"], default="public")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--as-of", default=None, help="題目的 as_of 日期（ISO）；預設今天。重生既有 release 時傳原 manifest 的 as_of")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="寫到這個目錄，不動 gold/releases（tools/reproduce.py 重生後和已提交的比對）")
    ap.add_argument("--pin-generator-version", default=None,
                    help="以這個版本標籤重生（已出貨的 release 用它 manifest 記的版本）；只能搭配 --out-dir")
    a = ap.parse_args()
    generate(a.family, a.release, a.split, a.n, out_dir=a.out_dir, as_of=a.as_of, pin_generator_version=a.pin_generator_version)
