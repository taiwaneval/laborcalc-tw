"""資料集登錄（repo 根目錄的 datasets.yaml）的讀取與檢查。

一個工作區、多個資料集：每個規則 family 登記在恰好一個資料集，每個 release 屬於一個資料集。
需要知道「這個 release／family 屬於哪個資料集」的程式都經過這裡，不各自寫死資料集名稱或 release。

datasets.yaml 的格式（datasets 底下的鍵是資料集 id，小寫英數與 -）：
  name                 對外名稱（index.json 的 index_name、匯出的 README 與 commit 訊息）
  families             這個資料集的 family id；一個 family 只能登記在一個資料集
  releases             由舊到新；最後一個是最新的 release
  release_name_format  新 release 的名稱，含 {period}（YYYYQn），例如 laborcalc-{period}
  legacy_releases      在格式訂定前就出貨、不改名的 release（可省略）
  default_weights      family → 權重；沒列的 family 權重 1（等權），可省略
  public.github        公開 GitHub repo（owner/name）
  public.huggingface   公開 Hugging Face dataset（owner/name）
  public.templates     匯出用的範本目錄（README.md、CITATION.cff、DATASET_CARD.md…），repo 相對路徑

用法：
  python3 tools/dataset_registry.py latest-release --family overtime_pay   # 該 family 所屬資料集的最新 release
  python3 tools/dataset_registry.py latest-release --dataset laborcalc
  python3 tools/dataset_registry.py check                                    # 只檢查登錄檔
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "datasets.yaml"
ID_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
PERIOD_RE = re.compile(r"\d{4}Q[1-4]\Z")
REQUIRED = ("name", "families", "releases", "release_name_format", "public")
PUBLIC_REQUIRED = ("github", "huggingface", "templates")


class RegistryError(ValueError):
    """登錄檔不合格式，或 release／family 與登錄檔對不上。訊息是給人看的一句話。"""


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    families: tuple[str, ...]
    releases: tuple[str, ...]
    release_name_format: str
    legacy_releases: tuple[str, ...] = ()
    default_weights: dict = field(default_factory=dict, hash=False)
    github: str = ""
    huggingface: str = ""
    templates: str = ""

    @property
    def latest_release(self) -> str | None:
        return self.releases[-1] if self.releases else None

    def release_name(self, period: str) -> str:
        """新 release 的名稱，例如 period=2027Q1 → laborcalc-2027Q1。"""
        if not PERIOD_RE.match(period):
            raise RegistryError(f"期別要寫成 YYYYQn（例如 2027Q1），得到 {period!r}")
        return self.release_name_format.replace("{period}", period)

    def accepts_release_name(self, release: str) -> bool:
        if release in self.legacy_releases:
            return True
        pre, _, post = self.release_name_format.partition("{period}")
        return (release.startswith(pre) and release.endswith(post)
                and bool(PERIOD_RE.match(release[len(pre):len(release) - len(post)])))

    def as_yaml_dict(self) -> dict:
        d = {"name": self.name, "families": list(self.families), "releases": list(self.releases),
             "release_name_format": self.release_name_format}
        if self.legacy_releases:
            d["legacy_releases"] = list(self.legacy_releases)
        d["default_weights"] = dict(self.default_weights)
        d["public"] = {"github": self.github, "huggingface": self.huggingface, "templates": self.templates}
        return d


class Registry:
    def __init__(self, datasets: list[Dataset], path: Path | None = None):
        self.datasets, self.path = datasets, path
        self._by_family = {f: d for d in datasets for f in d.families}
        self._by_release = {r: d for d in datasets for r in d.releases}

    def get(self, dataset_id: str) -> Dataset:
        for d in self.datasets:
            if d.id == dataset_id:
                return d
        raise RegistryError(f"datasets.yaml 沒有資料集 {dataset_id!r}；有的是 {[d.id for d in self.datasets]}")

    def dataset_of_family(self, family: str) -> Dataset | None:
        return self._by_family.get(family)

    def dataset_of_release(self, release: str) -> Dataset | None:
        return self._by_release.get(release)

    def resolve_release(self, release: str, families) -> Dataset:
        """這個 release 屬於哪個資料集。它的 family 必須都登記在同一個資料集；release 有登記時也必須是那個資料集。

        families 是 release 裡實際有的 family（manifest 或 index.json 的 families）。不合就丟 RegistryError。"""
        families = sorted(set(families))
        unregistered = [f for f in families if f not in self._by_family]
        if unregistered:
            raise RegistryError(f"release {release} 的 family {unregistered} 沒有登記在 datasets.yaml 的任何資料集；"
                                "先決定它屬於哪個資料集並加進該資料集的 families")
        found = {self._by_family[f].id: self._by_family[f] for f in families}
        if len(found) > 1:
            parts = "；".join(f"{d.name}：{sorted(f for f in families if self._by_family[f].id == i)}" for i, d in found.items())
            raise RegistryError(f"release {release} 混了兩個以上的資料集的 family（{parts}）。"
                                "一期 release 只能屬於一個資料集，請分成各自的 release")
        registered = self._by_release.get(release)
        if registered and found and registered.id not in found:
            only = next(iter(found.values()))
            raise RegistryError(f"release {release} 登記在 {registered.name}，裡面卻是 {only.name} 的 family {families}")
        if registered:
            return registered
        if found:
            return next(iter(found.values()))
        raise RegistryError(f"release {release} 沒有登記在 datasets.yaml，也沒有任何 family，無法判斷屬於哪個資料集")

    def latest_releases(self) -> list[tuple[Dataset, str]]:
        """每個有 release 的資料集與它的最新 release（make reproduce 與測試檢查的對象）。"""
        return [(d, d.latest_release) for d in self.datasets if d.latest_release]

    def latest_release_of_family(self, family: str) -> str:
        ds = self.dataset_of_family(family)
        if ds is None or not ds.latest_release:
            raise RegistryError(f"family {family} 沒有登記在任何資料集，或該資料集還沒有 release")
        return ds.latest_release

    def subset_yaml(self, dataset_id: str, releases: list[str] | None = None,
                    families: list[str] | None = None) -> str:
        """只含一個資料集的登錄檔（公開匯出用：公開 repo 只有這個資料集的 family，與匯出的那些 release）。
        families 給了時只留這些 family（匯出的 release 用到的；已登記但還沒進 release 的 family 不匯出，也不列）。"""
        d = self.get(dataset_id)
        if families is not None:
            unknown = [f for f in families if f not in d.families]
            if unknown:
                raise RegistryError(f"family {unknown} 沒有登記在資料集 {d.id}")
            d = replace(d, families=tuple(f for f in d.families if f in families))
        if releases is not None:
            unknown = [r for r in releases if r not in d.releases]
            if unknown:
                raise RegistryError(f"release {unknown} 沒有登記在資料集 {d.id}")
            d = replace(d, releases=tuple(r for r in d.releases if r in releases),
                        legacy_releases=tuple(r for r in d.legacy_releases if r in releases))
        return yaml.safe_dump({"datasets": {d.id: d.as_yaml_dict()}}, allow_unicode=True, sort_keys=False)


def _strs(v, what: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise RegistryError(f"{what} 要是字串清單（release 名稱請加引號，例如 \"2026Q4\"）")
    return tuple(v)


def parse(doc, path: Path | None = None) -> Registry:
    where = path.name if path else "datasets.yaml"
    if not isinstance(doc, dict) or not isinstance(doc.get("datasets"), dict) or not doc["datasets"]:
        raise RegistryError(f"{where} 要有 datasets: {{<id>: {{...}}}}")
    out = []
    for did, d in doc["datasets"].items():
        if not isinstance(did, str) or not ID_RE.match(did):
            raise RegistryError(f"資料集 id {did!r} 只能用小寫英數與 -，且以字母開頭")
        if not isinstance(d, dict):
            raise RegistryError(f"資料集 {did} 的內容要是 mapping")
        missing = [k for k in REQUIRED if k not in d]
        pub = d.get("public") or {}
        missing += [f"public.{k}" for k in PUBLIC_REQUIRED if not pub.get(k)]
        if missing:
            raise RegistryError(f"資料集 {did} 缺欄位：{missing}（name、families、releases、release_name_format、public）")
        fmt = d["release_name_format"]
        if not isinstance(fmt, str) or fmt.count("{period}") != 1:
            raise RegistryError(f"資料集 {did} 的 release_name_format 要含一個 {{period}}，得到 {fmt!r}")
        ds = Dataset(id=did, name=str(d["name"]), families=_strs(d["families"], f"{did}.families"),
                     releases=_strs(d["releases"], f"{did}.releases") if d["releases"] else (),
                     release_name_format=fmt,
                     legacy_releases=_strs(d.get("legacy_releases") or [], f"{did}.legacy_releases") if d.get("legacy_releases") else (),
                     default_weights={str(k): float(v) for k, v in (d.get("default_weights") or {}).items()},
                     github=str(pub["github"]), huggingface=str(pub["huggingface"]), templates=str(pub["templates"]))
        bad_names = [r for r in ds.releases if not ds.accepts_release_name(r)]
        if bad_names:
            raise RegistryError(f"資料集 {did} 的 release {bad_names} 不符合 {fmt}（期別 YYYYQn），也不在 legacy_releases")
        extra = sorted(set(ds.default_weights) - set(ds.families))
        if extra:
            raise RegistryError(f"資料集 {did} 的 default_weights 有不屬於它的 family：{extra}")
        out.append(ds)
    for attr, what in (("families", "family"), ("releases", "release")):
        seen: dict[str, str] = {}
        for ds in out:
            for x in getattr(ds, attr):
                if x in seen:
                    raise RegistryError(f"{what} {x} 同時登記在 {seen[x]} 與 {ds.id}；一個 {what} 只能屬於一個資料集")
                seen[x] = ds.id
    for attr in ("name", "github", "huggingface"):
        seen = {}
        for ds in out:
            v = getattr(ds, attr)
            if v in seen:
                raise RegistryError(f"{attr} {v} 同時用在 {seen[v]} 與 {ds.id}")
            seen[v] = ds.id
    return Registry(out, path)


def loads(text: str) -> Registry:
    return parse(yaml.safe_load(text))


def load(path: Path | str | None = None) -> Registry:
    p = Path(path) if path else REGISTRY
    if not p.exists():
        raise RegistryError(f"找不到資料集登錄檔 {p}")
    return parse(yaml.safe_load(p.read_text(encoding="utf-8")), p)


CATALOG_SCHEMA_VERSION = "0.1.0"     # index/datasets.json 的格式版本（tools/schemas/datasets_catalog.schema.json）


def catalog_bytes(root: Path | str) -> bytes:
    """index/datasets.json 的內容：root/datasets.yaml 的公開部分，加上每個 release 的 site-data 位置（還沒產生的為 null）。
    tools/site_data.py --catalog 寫的就是這個（另做 schema 驗證）；公開匯出用它替只含一個資料集的登錄檔產生目錄。"""
    import json
    root = Path(root)
    reg = load(root / "datasets.yaml")
    doc = {"schema_version": CATALOG_SCHEMA_VERSION, "datasets": [{
        "id": d.id, "name": d.name, "families": list(d.families), "latest_release": d.latest_release,
        "releases": [{"release": r, "site_data": f"index/{r}/site-data/" if (root / "index" / r / "site-data" / "manifest.json").exists() else None}
                     for r in d.releases],
        "public": {"github": d.github, "huggingface": d.huggingface},
    } for d in reg.datasets]}
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="資料集登錄（datasets.yaml）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lr = sub.add_parser("latest-release", help="印出資料集的最新 release")
    g = lr.add_mutually_exclusive_group(required=True)
    g.add_argument("--dataset")
    g.add_argument("--family", help="用 family 所屬的資料集")
    ck = sub.add_parser("check", help="只檢查登錄檔")
    for p in (lr, ck):
        p.add_argument("--registry", type=Path, default=REGISTRY)
    a = ap.parse_args(argv)
    try:
        reg = load(a.registry)
        if a.cmd == "check":
            print("✓ " + "；".join(f"{d.id}（{d.name}）：{len(d.families)} 個 family，最新 release {d.latest_release}"
                                   for d in reg.datasets))
            return 0
        ds = reg.get(a.dataset) if a.dataset else reg.dataset_of_family(a.family)
        if ds is None:
            raise RegistryError(f"family {a.family} 沒有登記在任何資料集")
        if not ds.latest_release:
            raise RegistryError(f"資料集 {ds.id} 還沒有 release")
    except RegistryError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(ds.latest_release)
    return 0


if __name__ == "__main__":
    sys.exit(main())
