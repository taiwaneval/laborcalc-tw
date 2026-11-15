"""產生架構圖：docs/architecture.svg 與 site/docs/architecture.html。

手寫 SVG 座標容易錯，所以用一份小規格描述 box / arrow，由程式排版。
"""
from __future__ import annotations

import html
from pathlib import Path

from paths import ROOT, SITE

W, H = 1120, 812
ACC = "var(--accent, #1f5f8b)"
SURF = "var(--surface, #fff)"

svg: list[str] = []


def text(x, y, s, size=11, anchor="start", weight=None, fill="currentColor", opacity=None, extra=""):
    attrs = f'x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{fill}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    if opacity:
        attrs += f' opacity="{opacity}"'
    svg.append(f"<text {attrs} {extra}>{html.escape(s)}</text>")


def box(x, y, w, h, title, subs, kind="solid", stroke="currentColor"):
    dash = ' stroke-dasharray="5 4"' if kind == "ext" else ""
    fill = "url(#hatch)" if kind == "private" else SURF
    sw = "1.6" if stroke != "currentColor" else "1.1"
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{SURF}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    if kind == "private":
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="none"/>')
    text(x + 10, y + 18, title, 12.5, weight="600")
    for i, s in enumerate(subs):
        text(x + 10, y + 34 + i * 14, s, 10.5, opacity="0.78")


def est_w(s, size):
    return sum(size * (1.0 if ord(c) > 0x2E7F else 0.58) for c in s)


def path(points, label=None, lpos=None, lanchor="start", color="currentColor", dash=False, lsize=10.5, rotate=None, backing=False):
    d = "M " + " L ".join(f"{x} {y}" for x, y in points)
    marker = "url(#arr-acc)" if color != "currentColor" else "url(#arr)"
    dd = ' stroke-dasharray="5 4"' if dash else ""
    svg.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.3"{dd} marker-end="{marker}"/>')
    if label and backing:
        w = est_w(label, lsize) + 8
        x0 = lpos[0] - w / 2 if lanchor == "middle" else lpos[0] - 4
        svg.append(f'<rect x="{x0:.0f}" y="{lpos[1] - lsize}" width="{w:.0f}" height="{lsize + 4}" rx="2" fill="{SURF}"/>')
    if label:
        extra = f'transform="rotate(-90 {lpos[0]} {lpos[1]})"' if rotate else ""
        text(lpos[0], lpos[1], label, lsize, anchor=lanchor, fill=color, extra=extra)


def band(y, h, label):
    svg.append(f'<rect x="20" y="{y}" width="1080" height="{h}" rx="6" fill="currentColor" opacity="0.045"/>')
    en, zh = label.split(" ", 1)
    text(30, y + 18, en, 10.5, weight="600", extra='letter-spacing="2"', opacity="0.7")
    text(30, y + 33, zh, 10.5, weight="500", opacity="0.7")


# ------------------------------------------------------------------ defs
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="TaiwanEval 資料架構：法規快照（bronze）經盤點、複核與規則編碼（silver）生成公開與私有題集（gold），評測 log 聚合成指數；人工簽核與社群回報進入 silver 的 reviews，法規變更以 content hash 偵測。" font-family="\'Noto Sans TC\',\'PingFang TC\',system-ui,sans-serif">')
svg.append('<defs>'
           '<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="currentColor"/></marker>'
           f'<marker id="arr-acc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{ACC}"/></marker>'
           '<pattern id="hatch" patternUnits="userSpaceOnUse" width="8" height="8"><path d="M0 8 L8 0" stroke="currentColor" stroke-width="0.7" opacity="0.3"/></pattern>'
           '</defs>')

# ------------------------------------------------------------------ bands
band(96, 84, "BRONZE 法規快照")
band(196, 200, "SILVER 規則・複核")
band(412, 120, "GOLD 題集")
band(548, 112, "EVAL 評測・指數")
band(676, 84, "SITE 公開")

# ------------------------------------------------------------------ external
box(110, 24, 230, 44, "全國法規資料庫 開放資料", ["law.moj.gov.tw · 每日批次 XML/JSON"], kind="ext")
box(380, 24, 250, 44, "勞動部・財政部 公告／費率表", ["最低工資、級距、行事曆（多為 PDF）"], kind="ext")

# ------------------------------------------------------------------ bronze
box(110, 108, 330, 60, "bronze/<pcode>/", ["source.json · provenance.json（sha256、鏡像 commit、抓取時間）", "只進不改；復現的錨點"])
path([(225, 68), (225, 108)], "fetch_law.py · 每夜", (231, 92))

# ------------------------------------------------------------------ silver
box(110, 212, 180, 64, "inventory.json", ["條／項 unit · content_hash", "條文互引（第 N 條第 M 項）"])
box(355, 212, 185, 64, "triage.yaml", ["LLM 第一輪 + heuristic 標記", "computable / lookup / … 五類"])
box(605, 212, 245, 64, "families/<id>/", ["spec.yaml · rate_tables · golden cases", "rule.py ≡ rule.js（CI 1000 組一致）"])
box(340, 300, 210, 64, "reviews/*.yaml", ["人工簽核：confirm／override", "具名、日期、對 spec／rule hash"], stroke=ACC)
box(890, 300, 210, 64, "律師・會計師・社群", ["具名簽核；匿名回報只當線索", "署名進頁面"], kind="ext", stroke=ACC)

path([(225, 168), (225, 212)], "build_inventory.py", (231, 196))
path([(400, 168), (400, 212)], "content_hash 變了 → 標 stale", (406, 196), dash=True)
path([(290, 244), (355, 244)], "triage.py", (322, 237), lanchor="middle", lsize=10)
path([(540, 244), (605, 244)], "computable", (572, 237), lanchor="middle", lsize=10)
path([(445, 300), (445, 276)], "apply_review.py", (451, 292), color=ACC)
path([(550, 320), (720, 320), (720, 276)], "family 簽核（reviews.yaml）", (560, 334), color=ACC)
path([(890, 345), (550, 345)], "", None, color=ACC)
path([(505, 68), (505, 88), (700, 88), (700, 212)], "人工整理 rate_tables（帶生效期間、來源 URL）", (706, 150), dash=True)

# ------------------------------------------------------------------ gold
box(110, 428, 230, 72, "public/ *.jsonl", ["公開 salt · 進 repo", "任何人跑一次得到同一份"])
box(400, 428, 280, 72, "gold/generate.py", ["分支配額 · 邊界值 · 實體池 · 數字旋鈕", "每題記 spec／rule／bronze hash、triage 版本、seed"])
box(740, 428, 230, 72, "private/ *.jsonl", ["秘密 salt（env）· 不進 repo", "下一版 release 時轉公開"], kind="private")
path([(800, 276), (800, 400), (540, 400), (540, 428)], "spec · rule · templates", (670, 394), lanchor="middle")
path([(400, 464), (340, 464)], "公開 salt", (370, 457), lanchor="middle", lsize=10)
path([(680, 464), (740, 464)], "秘密 salt", (710, 457), lanchor="middle", lsize=10)

# ------------------------------------------------------------------ eval
box(110, 564, 210, 72, "Inspect AI 評測", ["task = family · solver = 受測模型", "單位 model@quant@hardware"])
box(360, 564, 200, 72, "logs/", ["每題 prompt · response · grade", "公開；可重新評分"])
box(600, 564, 200, 72, "aggregate.py", ["預設權重寫成程式", "任何人可 fork 重算"])
box(840, 564, 260, 72, "index/<release>", ["public 分 · private 分", "兩者分歧 ＝ 汙染訊號"], stroke=ACC)
path([(225, 500), (225, 564)], "public 題集", (231, 540))
path([(855, 500), (855, 520), (260, 520), (260, 564)], "private 題集", (560, 509), lanchor="middle")
path([(320, 600), (360, 600)])
path([(560, 600), (600, 600)])
path([(800, 600), (840, 600)], "加權", (820, 593), lanchor="middle", lsize=10)

# ------------------------------------------------------------------ site
box(110, 692, 220, 56, "GitHub repo", ["bronze／silver／gold-public 全進", "issue · PR 是審計軌跡"])
box(360, 692, 220, 56, "/rules/", ["silver 渲染：規則卡、盤點、變更流", "每頁：回報問題 · 我來複核"])
box(610, 692, 220, 56, "/datasets/<release>", ["gold public + manifest", "lineage 可追到 bronze hash"])
box(860, 692, 240, 56, "/index/<release>", ["分數 + logs + aggregate.py", "pin 住的 family／triage 版本"])
path([(470, 692), (470, 684), (95, 684), (95, 380), (445, 380), (445, 364)], "社群：issue／PR → reviews", (102, 374), color=ACC)

# ------------------------------------------------------------------ legend
ly = 782
svg.append(f'<rect x="110" y="{ly-9}" width="22" height="12" rx="2" fill="{SURF}" stroke="currentColor"/>'); text(138, ly, "進 repo／公開", 10.5)
svg.append(f'<rect x="240" y="{ly-9}" width="22" height="12" rx="2" fill="{SURF}" stroke="currentColor" stroke-dasharray="4 3"/>'); text(268, ly, "外部來源／人", 10.5)
svg.append(f'<rect x="370" y="{ly-9}" width="22" height="12" rx="2" fill="{SURF}" stroke="currentColor"/><rect x="370" y="{ly-9}" width="22" height="12" rx="2" fill="url(#hatch)"/>'); text(398, ly, "不公開（private）", 10.5)
svg.append(f'<path d="M520 {ly-3} L550 {ly-3}" stroke="{ACC}" stroke-width="1.3" marker-end="url(#arr-acc)"/>'); text(558, ly, "人工／社群介入", 10.5)
svg.append(f'<path d="M680 {ly-3} L710 {ly-3}" stroke="currentColor" stroke-width="1.3" stroke-dasharray="5 4" marker-end="url(#arr)"/>'); text(718, ly, "變更偵測／人工整理", 10.5)
svg.append('</svg>')

SVG = "\n".join(svg)

# standalone SVG for docs/
standalone = SVG.replace('<svg ', '<svg style="color:#1d2024;background:#f5f4ef" ', 1).replace("var(--accent, #1f5f8b)", "#1f5f8b").replace("var(--surface, #fff)", "#ffffff")
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "architecture.svg").write_text(standalone)

PAGE = f"""<title>TaiwanEval 資料架構</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@700&family=Noto+Sans+TC:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap">
<style>
/* Layout: one figure, full width, then a short reading guide. Same tokens as the rule card. */
:root{{
  --bg:#f5f4ef; --fg:#1d2024; --fg-2:#5b6068; --line:#d8d6cc; --surface:#ffffff; --accent:#1f5f8b;
  --display:'Noto Serif TC','PMingLiU',serif; --body:'Noto Sans TC','PingFang TC','Microsoft JhengHei',system-ui,sans-serif; --mono:'JetBrains Mono',ui-monospace,monospace;
}}
@media (prefers-color-scheme: dark){{ :root:not([data-theme="light"]){{ --bg:#17191c; --fg:#e6e4df; --fg-2:#9ea3ab; --line:#30343a; --surface:#1f2226; --accent:#7fb2d9; color-scheme:dark }} }}
:root[data-theme="dark"]{{ --bg:#17191c; --fg:#e6e4df; --fg-2:#9ea3ab; --line:#30343a; --surface:#1f2226; --accent:#7fb2d9; color-scheme:dark }}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:var(--body);font-size:15px;line-height:1.7}}
.wrap{{max-width:1180px;margin:0 auto;padding-block:28px 64px;padding-inline:16px}}
h1{{font-family:var(--display);font-size:clamp(24px,4vw,32px);margin:0 0 6px;text-wrap:balance}}
.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-2);font-weight:500}}
figure{{margin:22px 0 0;overflow-x:auto;padding-block:8px}}
figure svg{{display:block;width:100%;min-width:900px;height:auto;color:var(--fg)}}
figcaption{{font-size:13.5px;color:var(--fg-2);max-width:72ch;margin-top:10px}}
.guide{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px 28px;margin-top:28px;padding-top:22px;border-top:1px solid var(--line)}}
.guide>div{{min-width:0}}
.guide h3{{font-family:var(--body);font-size:12.5px;letter-spacing:.06em;color:var(--fg-2);font-weight:600;margin:0 0 4px}}
.guide p{{margin:0;font-size:14px}}
.mono{{font-family:var(--mono);font-size:.9em}}
pre{{font-family:var(--mono);font-size:12.5px;line-height:1.55;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;overflow-x:auto;margin:22px 0 0}}
</style>
<div class="wrap">
<div class="eyebrow">TaiwanEval · Rules Registry · Architecture</div>
<h1>TaiwanEval 資料架構</h1>
<figure>
{SVG}
<figcaption>資料由上往下流：法規快照只進不改；silver 是唯一有人簽核的層，也是變更偵測的落點；gold 以 salt 切成公開與私有兩半；指數是 logs 上的聚合，權重是程式碼。藍色箭頭是人介入的地方。</figcaption>
</figure>

<div class="guide">
  <div><h3>BRONZE</h3><p>開放資料的快照加 provenance。沒人審它，但沒有它 silver 的 content hash 就沒有錨；法規修正靠逐 unit 比 hash 偵測。</p></div>
  <div><h3>SILVER</h3><p>盤點、triage、reviews、family spec 全在這層，人工簽核只發生在這層。整層公開沒有代價，內容本來就是公開法規的整理。</p></div>
  <div><h3>GOLD</h3><p>生成器與公開 salt 進 repo，任何人可重現 public 題集；private 題集用秘密 salt，下一版 release 時轉公開。指數同時報兩個分數。</p></div>
  <div><h3>EVAL</h3><p>Inspect AI 的 logs 公開，<span class="mono">aggregate.py</span> 把加權寫成預設參數。「為什麼這樣加權」變成「這是預設值，你可以改」。</p></div>
  <div><h3>SITE</h3><p><span class="mono">/rules/</span> 是 silver 的渲染，每頁有回報與複核入口；<span class="mono">/index/&lt;release&gt;</span> pin 住當期用的 family 與 triage 版本。</p></div>
  <div><h3>LINEAGE</h3><p>每題帶 spec／rule／bronze 的 sha256、triage 版本、seed。從一個指數數字可以追到那天抓的法條快照。</p></div>
</div>

<pre>taiwaneval-index/
├─ bronze/&lt;pcode&gt;/            source.json · provenance.json          只進不改
├─ silver/
│  ├─ inventory/&lt;pcode&gt;/      inventory.json · triage.yaml · families.yaml · reviews/*.yaml
│  └─ families/&lt;id&gt;/          spec.yaml · rule.py · rule.js · templates.yaml · test_agreement.py
├─ gold/
│  ├─ generate.py
│  └─ releases/&lt;release&gt;/public/&lt;family&gt;.jsonl · .manifest.json · .coverage.json   （private/ 不進 repo）
├─ tools/                     fetch_law · build_inventory · triage · apply_review · coverage_report · build_page
├─ site/                      CI 產物：rules/laws/ · rules/families/ · docs/
└─ docs/architecture.svg</pre>
</div>
"""
(SITE / "docs").mkdir(parents=True, exist_ok=True)
(SITE / "docs" / "architecture.html").write_text(PAGE)
print("wrote docs/architecture.svg and site/docs/architecture.html")
