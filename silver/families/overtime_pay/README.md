# overtime_pay — 加班費（月薪制・單日）

TaiwanEval Tier 1 rule family 原型。

```
spec.yaml            source of truth（法源、範圍、參數、費率表、公式、分支配額、golden cases、審閱紀錄）
rule.py              Python 實作（pipeline 驗證器）
rule.js              JS 實作（規則卡頁面試算）；與 rule.py 獨立撰寫
test_agreement.py    golden cases + 1000 組隨機輸入，檢查兩份實作一致
dist/                build_page.py 產出的 HTML（勿手改）
```

工作流程

```
python3 families/overtime_pay/test_agreement.py     # 必須 PASS
python3 tools/build_page.py families/overtime_pay   # 產出 dist/overtime_pay.html 與 .artifact.html
git tag rules/overtime_pay/v0.1.0                   # 凍結；頁面印的 spec sha256 即簽核對象
```

改規則的順序：改 spec.yaml → 改 rule.py 與 rule.js → test 過 → rebuild → bump version → tag。
