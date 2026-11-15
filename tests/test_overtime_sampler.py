import hashlib, importlib.util, json, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import yaml


def load(fid, name):
    p = ROOT / "silver" / "families" / fid / f"{name}.py"
    s = importlib.util.spec_from_file_location(f"{fid}_{name}", p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def test_overtime_sampler_reproduces_release_items():
    """sampler 契約：每題的 params 由該題 seed 的 rng 經 sample() 得到（配對題的 partner 沿用 anchor 的 params）。
    模板、姓名與題幹由 gold/generate.py 決定（含去重重抽、分支內模板平均），由 test_generate_committed_identical 驗證。"""
    smp = load("overtime_pay", "sampler")
    spec = yaml.safe_load((ROOT / "silver/families/overtime_pay/spec.yaml").read_text())
    tpl = yaml.safe_load((ROOT / "silver/families/overtime_pay/templates.yaml").read_text())
    import dataset_registry
    rel = dataset_registry.load().latest_release_of_family("overtime_pay")
    pub = ROOT / "gold/releases" / rel / "public"
    items = [json.loads(l) for l in (pub / "overtime_pay.jsonl").read_text().splitlines() if l.strip()]
    salt = json.loads((pub / "overtime_pay.manifest.json").read_text())["salt"]
    master = random.Random(hashlib.sha256(f"{salt}|{rel}|overtime_pay|-1".encode()).hexdigest())
    plan = smp.plan(spec, 200, master)
    assert [p["branch"] for p in plan] == [it["branch"] for it in items]
    anchors = {it["pair_id"]: it for it in items if it["pair_id"] and it["item_id"].endswith(it["pair_id"][-4:])}
    for p, it in zip(plan, items):
        if it["pair_id"] and it is not anchors[it["pair_id"]]:
            assert it["params"] == anchors[it["pair_id"]]["params"]
            continue
        assert smp.sample(random.Random(it["seed"]), p) == it["params"]
        fields, _ = smp.context(random.Random(0), it["params"], tpl)
        assert all(any(f in it["prompt"] for f in s) for s in smp.visible(it["params"], {**fields}))