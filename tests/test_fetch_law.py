"""bronze 快照：source.json 存鏡像原檔的原樣 bytes，provenance 的 sha256 才能直接拿 repo 裡的檔驗證。

2026-10-08 的快照把原檔重新排版（tab 縮排 → 1 格空白）後才存，provenance 記的卻是原檔的 sha256，
repo 裡的 source.json 永遠對不上自己的 provenance。
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
_s = importlib.util.spec_from_file_location("fetch_law_under_test", ROOT / "tools/fetch_law.py")
fl = importlib.util.module_from_spec(_s); _s.loader.exec_module(fl)

# 鏡像原檔用 tab 縮排：重新排版就會改變 bytes
RAW = '{\n\t"法規名稱": "測試法",\n\t"法規網址": "https://law.moj.gov.tw/x",\n\t"最新異動日期": "20240731",\n\t"法規內容": []\n}'.encode()


def fake_mirror(tmp_path: Path, pcode: str) -> Path:
    m = tmp_path / "mirror"
    (m / "FalVMingLing").mkdir(parents=True)
    (m / "FalVMingLing" / f"{pcode}.json").write_bytes(RAW)
    (m / "UpdateDate.txt").write_text("20260918\n")
    git = ["git", "-C", str(m), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
           "-c", "core.hooksPath=/dev/null"]
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "mirror"]):
        subprocess.run([*git, *cmd], check=True)
    return m


def test_snapshot_keeps_the_mirror_bytes_so_the_recorded_hash_verifies(tmp_path, monkeypatch):
    mirror = fake_mirror(tmp_path, "X0000001")
    monkeypatch.setattr(fl, "bronze_dir", lambda pcode: tmp_path / "bronze" / pcode)
    fl.main(["X0000001", "--mirror", str(mirror)])
    out = tmp_path / "bronze/X0000001"
    prov = json.loads((out / "provenance.json").read_text())
    assert (out / "source.json").read_bytes() == RAW
    assert hashlib.sha256((out / "source.json").read_bytes()).hexdigest() == prov["sha256"]
    assert (prov["law_name"], prov["last_amended"], prov["mirror_update_date"]) == ("測試法", "20240731", "20260918")


_f = importlib.util.spec_from_file_location("fetch_source_for_law_test", ROOT / "tools/fetch_source.py")
fsrc = importlib.util.module_from_spec(_f); _f.loader.exec_module(fsrc)


@pytest.mark.parametrize("pdir", sorted(p for p in (ROOT / "bronze").iterdir() if p.is_dir()), ids=lambda p: p.name)
def test_committed_snapshot_matches_its_provenance(pdir):
    """快照檔由 provenance 決定：法規（沒有 source_type）是 source.json，非法規來源是 provenance 的 source_file。"""
    prov = json.loads((pdir / "provenance.json").read_text())
    snap = fsrc.snapshot_file(pdir, prov)
    assert hashlib.sha256(snap.read_bytes()).hexdigest() == prov["sha256"], \
        f"{pdir.name}/{snap.name} 的 sha256 與 provenance.json 不同：bronze 只能由 tools/fetch_law.py、tools/fetch_source.py 寫入"
    assert fsrc.check_snapshot(pdir) == []


def test_the_snapshot_file_comes_from_the_provenance_not_a_fixed_name(tmp_path):
    d = tmp_path / "bronze/test-fake-dataset"
    d.mkdir(parents=True)
    (d / "source.csv").write_bytes(b"a,b\n1,2\n")
    prov = {"source_type": "dataset", "source_file": "source.csv"}
    assert fsrc.snapshot_file(d, prov) == d / "source.csv"
    assert fsrc.snapshot_file(tmp_path / "bronze/N0030001", {"source_file": "FalVMingLing/N0030001.json"}).name == "source.json"
