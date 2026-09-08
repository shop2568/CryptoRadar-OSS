# V238 Compression → Expansion

## 1. 2025 contamination audit

2025_SELECTION_CONTAMINATION = FALSE（依可稽核使用路徑，非宣稱untouched）。
V237事故不是「尚未simulate立即阻擋」：已完成部分2025計算，再由late gate中止。現有執行紀錄暴露進度/候選數，未輸出2025績效表或逐筆winner/loser身份供選參。V238僅沿使用者指定15m families，natural constants於觀察前寫入PROTOCOL；feature只讀entry_time/signal_time/direction與OHLCV，無V237 outcome/身份依賴。Research loader只讀2023/24。證據SHA見V238_CONTAMINATION_AUDIT.json。不能證明「2025從未計算」，也不以本次稽核抹去事故。

## 2. Data coverage

106 post-transform opportunities；94 baseline Research trades中91筆完整（全部LONG/Binance），3SHORT缺原signal_time（GMX/TRX/AIOT），strict missing、沒有猜entry-15m。3筆仍保留於FEATURES，不当neutral或good/bad。無跨交易所證據。期間沿用frozen2023/2024標籤，非另換曆年。

使用原transformed signal_time為signal candle；若延遲entry仍保留較早signal，描述的是原訊號，不是事後挑K。支撐壓力reference是明訂prior16 high/low觀察proxy，不冒稱每個module的原trigger。38連續15m input，ATR20/medianvolume等只取signal以前，signal必須+15m<=entry。詳細自然門檻、priority見PROTOCOL。

## 3. Feature table（LONG TP1 rate）

| Feature | 2023 | 2024 | monotonic | stable | verdict |
|---|---|---|---|---|---|
| Compression | mild19.35%、expanding21.43% | mild29.41%、expanding30% | 不支持壓縮更好 | 否 | strong0筆、nobase每年1筆 |
| Volume build | spike-only16.22%、flat42.86% | spike-only24%、flat33.33% | healthy/exhausted太少 | Top3不過 | 不足建gate |
| Candle quality | weak12%、clean28.57% | weak20%、clean36% | 局部兩組一致 | LONG局部Top1/3過；完整門檻不過 | 刪weak會犧牲太多winner |
| Transition | spike17.78%、weak100%(1筆) | spike26.47%、weak37.5% | healthy/weak/spike完整梯度無法證明 | 否 | 2023對照僅1筆 |

沒有WICK_REJECTION/OVERSIZED_EXHAUSTION完整成交樣本；HEALTHY_TRANSITION只有2024一筆，不能以100%勝率當edge。LOW/MILD/EXPANDING等非調整成最佳分位，不再細切。

V238_GROUPS.csv輸出year/side/entry_mode及combined的Trades/TP1/TP1rate/Loss/FullStop/AvgR/Net。FullStop取frozen原停損reason，非所有非TP1。V238_SELECTIVITY.csv包括每年與combined Top1/3正負tail去除；symbol數與exchange數均保存。符號分類不是統計顯著性檢定。

## 4. 最有局部分離的adverse：WEAK_BREAK

完整LONG母群91筆：69 net losers、22 TP1 winners。
WEAK_BREAK45筆，38 losers、7 TP1；Loss rejection38/69=55.07%，winner sacrifice7/22=31.82%，selective efficiency=+23.25百分點。局部Top1/3後selective efficiency仍正，不是單symbol；但所有樣本同交易所，且原winner保留僅68.18%，遠低90%。即使用全部94筆母群，7/23 winner損失仍超過10%。不因有正efficiency就PASS。

允許variant針對的SPIKE_BREAKOUT更不適合：79/91筆，62losers、17TP1。Loser rejection89.86%，winner sacrifice77.27%，efficiency+12.58pp；trade崩落與winner損失過大。NO_BASE僅2筆、REJECTION0筆，不可硬選。

## 5. Research selected variant

NONE。沒有建立A/B/C/D或PREDECLARED_RULES；沒有刪單重算冒充portfolio回測。WEAK_BREAK的局部quality差異不等於有符合限定transition states與winner-retention門檻的Challenger。

## 6. Validation

NOT_RUN。V238未載入2025 outcome或模擬2025，未做新選參。不是把V237事故當合法validation結果。

## 7. TOTAL

| Variant | Trades | TP1 | Win Rate | Net U | PF | DD% | Trade Retention | Winner Retention |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Frozen V188 |213|77|36.1502%|3461.500976|2.549224|16.9458|100%|100%|
| V238 |未建立Challenger|—|—|—|—|—|—|—|

## 8. 結論與review

最終補驗 Top5：WEAK_BREAK 移除 Top1/3/5 winner 或 loser 後，兩年 selective efficiency 仍正；SPIKE_BREAKOUT 的 Top5 跨年條件不通過。完整逐項結果見 V238_SELECTIVITY.csv / V238_STABILITY.csv。這不解除 WEAK_BREAK 的 31.82% winner sacrifice 與單交易所限制，因此 Phase 1 FAILED，研究封存，不建立 V239。

V238_FAILED_NO_STABLE_BREAKOUT_QUALITY_SIGNAL / FAILED_維持V188。
這裡「無stable」指沒有符合完整穩健與保留要求的可執行gate，不代表所有feature都毫無描述性差異。局部weak/clean分離明確保留，不否認它。

8/8 causal tests PASS：future/forming排除、缺bar、缺signal不fallback、零volume不補值、方向mirror、outcome mutation、candidate欄位白名單。baseline來源SHA重驗不變。Independent review確認分類優先次序使spike涵蓋多數訊號，這是本粗定義限制，不用outcome調整使其變漂亮；缺SHORT與單交易所是覆蓋限制。

沒有full-market variant replay、沒有改V188/V009/V010/V219/V221C/V235/W2/W4、無Forward/service/task/cloud。

下一個唯一建議family：**同期市場廣度與個幣相對強弱**，先查既有研究是否已涵蓋，不啟動V239。
