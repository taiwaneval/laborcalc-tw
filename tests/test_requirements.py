"""requirements.txt：完整鎖定的依賴清單；乾淨 clone 只用它安裝（tools/reproduce.py）。"""
import re
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([A-Za-z0-9_.+!\-]+)$")
DIRECT = ("inspect_ai", "PyYAML", "Jinja2", "pytest", "anthropic", "openai")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins() -> dict[str, str]:
    out = {}
    for ln in (ROOT / "requirements.txt").read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = PIN.match(ln)
        assert m, f"requirements.txt 每一行都要釘確切版本（name==version）：{ln!r}"
        out[m.group(1)] = m.group(2)
    return out


def test_every_line_is_an_exact_pin_and_the_direct_dependencies_are_there():
    got = {canonical(k): v for k, v in pins().items()}
    assert {canonical(d) for d in DIRECT} <= set(got)


def test_this_environment_matches_the_lock():
    """測試跑在鎖定的環境裡。升級了套件卻沒更新 requirements.txt，乾淨 clone 的結果就可能和這裡不同。"""
    def installed(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None
    drift = {name: (want, installed(name)) for name, want in pins().items() if installed(name) != want}
    assert not drift, f"requirements.txt 與目前環境不一致（鎖定, 已安裝）：{drift}"
