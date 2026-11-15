"""index 頁標示 preview／release，preview 時列出擋住 release 的項目。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
_s = importlib.util.spec_from_file_location("build_index_page_under_test", ROOT / "tools/build_index_page.py")
bip = importlib.util.module_from_spec(_s); _s.loader.exec_module(bip)

R = "2099Q1"
import dataset_registry  # noqa: E402

LATEST = dataset_registry.load().get("laborcalc").latest_release   # 頁面樣板用 LaborCalc-TW 最新 release 的 index


def render(tmp_path, monkeypatch, **override) -> str:
    idx = json.loads((ROOT / "index" / LATEST / "index.json").read_text())
    idx["release"] = R
    for k, v in override.items():
        if v is None:
            idx.pop(k, None)
        else:
            idx[k] = v
    (tmp_path / "index" / R).mkdir(parents=True)
    (tmp_path / "index" / R / "index.json").write_text(json.dumps(idx, ensure_ascii=False))
    monkeypatch.setattr(bip, "INDEX", tmp_path / "index")
    monkeypatch.setattr(bip, "SITE", tmp_path / "site")
    bip.build(R)
    return (tmp_path / "site" / "index" / f"{R}.html").read_text()


def test_preview_index_is_labelled_and_lists_release_blockers(tmp_path, monkeypatch):
    html = render(tmp_path, monkeypatch, mode="preview", release_blockers=["overtime_pay/public：沒有對應的 approved 簽核"])
    assert 'data-mode="preview"' in html and "不是正式 release" in html
    assert "overtime_pay/public：沒有對應的 approved 簽核" in html


def test_release_index_is_labelled_without_a_blocker_box(tmp_path, monkeypatch):
    html = render(tmp_path, monkeypatch, mode="release", release_blockers=[])
    assert 'data-mode="release"' in html and "不是正式 release" not in html


def test_index_built_before_modes_existed_renders_as_preview(tmp_path, monkeypatch):
    html = render(tmp_path, monkeypatch, mode=None, release_blockers=None)
    assert 'data-mode="preview"' in html


def _pins_with_as_of() -> dict:
    idx = json.loads((ROOT / "index" / LATEST / "index.json").read_text())
    pins = idx["pins"]
    for fam, splits in pins.items():
        for p in splits.values():
            p["as_of"] = "2026-10-09"; p.setdefault("rejected", 0)   # 固定值，不受真實 index.json 的 as_of 影響
    return pins


def test_index_page_describes_each_family_from_its_own_files(tmp_path, monkeypatch):
    """fake solver 與對照題的說明取自各 family 的 hypotheses.py 與 spec，不寫死加班費。"""
    html = render(tmp_path, monkeypatch, pins=_pins_with_as_of())
    assert "4/3" in html and "365" in html                       # 兩個 family 的 naive 錯法都在
    assert "W0" in html and "S0" in html                          # 兩個 family 的對照分支都在
    assert "無加班應答 0" not in html                              # 舊的寫死例子


def test_recompute_section_lists_every_family_with_its_as_of(tmp_path, monkeypatch):
    html = render(tmp_path, monkeypatch, pins=_pins_with_as_of())
    for fam in ("overtime_pay", "annual_leave_days"):
        assert f"gold/generate.py {fam} --release {R} --split public --n 200 --as-of 2026-10-09" in html
    assert "-T family=overtime_pay" not in html



def test_recompute_section_starts_with_the_one_command_reproduction(tmp_path, monkeypatch):
    """release 頁要告訴第三方：一個指令就能從乾淨 clone 逐層重建、比對這頁的題集與 index。"""
    html = render(tmp_path, monkeypatch, pins=_pins_with_as_of())
    recompute = html[html.index('id="recompute"'):]
    assert "make reproduce ONLINE=1" in recompute
    assert recompute.index("make reproduce") < recompute.index("gold/generate.py")
    section = recompute[:recompute.index("</section>")]
    assert section.count("<pre>") == section.count("</pre>") == 1     # 同一個指令區塊，不是巢狀的兩個 <pre>


def test_pins_show_all_four_signed_hashes(tmp_path, monkeypatch):
    pins = _pins_with_as_of()
    for splits in pins.values():
        for p in splits.values():
            p.setdefault("templates_sha256", "a" * 64); p.setdefault("sampler_sha256", "b" * 64)
    html = render(tmp_path, monkeypatch, pins=pins)
    assert "templates sha256" in html and "sampler sha256" in html
