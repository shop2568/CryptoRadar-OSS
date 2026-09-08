# CryptoRadar 研究交接｜V234 reviewer 收尾

## V238 Compression Expansion（2026-09-08，最新）

- 最終收尾：Top1/3/5 winner/loser removal 已完成；WEAK_BREAK 兩年保持正效率，但 winner sacrifice 31.82% 不合格；SPIKE_BREAKOUT Top5 跨年不通過。Phase 1 FAILED，V238 封存、不進2025、不開V239。
- 使用者要求公開備份至 shop2568/CryptoRadar-OSS：雲端 radar.service 實際主程序 radar_v010.py 已以 PID/cmdline/source SHA 核對，備份採雲端下載來源；僅 allowlisted source/research/report，排除 env/log/state/cache。公開來源及掃描證據隨備份保存，commit SHA 以 GitHub 最後提交及本機備份收據為準。

- 目錄V238_COMPRESSION_EXPANSION。先稽核V2372025誤觸：有simulation但無該期metrics/winner身份用於V238選參，SELECTION_CONTAMINATION=FALSE；不是untouched，保留事故限制與證據SHA。
- 只跑2023/24 observation，106posttransform/94baseline；91LONG完整、3SHORT缺signal_time不猜。所有完整樣本Binance。Natural coarse定義見PROTOCOL，未grid。
- WEAK_BREAK vs CLEAN局部兩年有分離、Top1/3 efficiency正，但擋weak犧牲7/22=31.82%完整winner；SPIKE擋79/91、犧牲17/22=77.27%。NO_BASE2、REJECTION0、HEALTHY1不足。無符合完整穩定+winner保留要求的gate。
- 結論V238_FAILED_NO_STABLE_BREAKOUT_QUALITY_SIGNAL / FAILED_維持V188，承認局部quality差異不是毫無edge。未建variant/predeclare、未做dynamic replay、未讀2025 outcome、不啟動V239。
- 8/8tests PASS，FEATURES/GROUPS/SELECTIVITY/STABILITY/CONTAMINATION_AUDIT/SUMMARY/REPORT保存。Baseline來源SHA不變，受保護策略不改，無Forward/collector/task/cloud。
- 下一family僅建議同期市場廣度與個幣相對強弱；未開始。

## V237 H1/H4 Momentum Regime（2026-09-08，最新）

- 新目錄V237_H1_H4_MOMENTUM_REGIME。Phase1研究94成交全部HTF完整（91LONG/3SHORT，全Binance）；106 posttransform有1BGB缺資料但原不進prepared。無missing補neutral。
- 局部LONG HTF_DETERIORATING兩年較差且Top1/3通過；但HTF_ADVERSE並非差組，三狀態不單調；H1REVERSING Top3失效。不宣稱整family無訊號。
- 預宣告指定C聯集並只做研究區full-market重播：63/17/WR26.9841%/Net114.835491/PF1.243821/DD9.1488；base研究94/23/Net-2.524965/PF0.996669/DD16.9458。身分Retention60/94=63.8298%、3補位、34原單消失、winner retention15/23。未達WR/PF/retention，FAILED_維持V188，研究winner NONE。
- 103prepared identity/order exact，PnL delta117.360457 reconcile PASS。去Top1/3/5delta優勢68.69/13.42/-10.69。18tests PASS。
- 重要治理事故：一次舊引擎hardcoded三年loop進2025 simulation後才被late allocation guard拒絕。沒有使用2025績效選規則，但不得宣稱整輪untouched validation。修為in-memory research-only年份loop+summary comprehension及run前IO guard，最後成功run只有2023/24。原策略source未改，原predeclare SHA留存、最後runner SHA另記。
- V237_REPORT/SUMMARY/RESEARCH_REPLAY/FEATURE_TABLE/COVERAGE/STABILITY/DIFF/TOP_DEPENDENCE/年方向及raw/checkpoints已存。正式三年V237結果不存在，研究不合格停止，不換规则、不進2025、不開V238。
- 下一family僅建議15m突破前壓縮擴張序列；未實作。V188/V009/V010/FrozenV219/V221C/V235/W2/W4未改，無Forward/collector/task/cloud。

## V236 Phase1 收尾（2026-09-08，本節取代舊BLOCKED狀態）

- 完整補查完成：V236_PHASE1_FINAL.md及WINNER_LOSER/CONCENTRATION/MISSING_SENSITIVITY/MONOTONIC四CSV。Tests18/18。75完整17TP1/-86.115574U；19missing6TP1/+83.590609U，全部3筆SHORT都missing，coverage bias重大，不能推廣完整組結果。四大family未有穩定支持，V236_FAILED_NO_STABLE_D1_SIGNAL，停止。下一family僅三項診斷建議（1H/4H動能轉折、突破前壓縮擴張轉換、同期廣度/相對強弱），未開始任何新版本。

- 使用者接受frozen16-key/9-block：BASELINE_FROZEN_SEMANTICS_ACCEPTED；三筆legacy OPEN只保留provenance，不重建V186 state。
- baseline exact與source SHA仍一致，不重跑。新增d1_features.py/research_audit.py/test_d1_features.py。
- 只跑2023+2024：106 post-transform opportunities；94 baseline trades，75完整D1、19不足55連續日，缺失仍保留。完整樣本全部LONG。
- Countertrend/extension跨年反轉；room在Entry→TP1組未更差；沒有支持指定gate的穩定關係。詳見RESEARCH_BUCKETS/EDGE_AUDIT。
- 結論V236_FAILED_NO_STABLE_D1_SIGNAL / FAILED_維持V188。遵守Phase1停止條件：無predeclared variants、無Research winner、無2025 validation、無variant full-market replay；不是legacy timing再阻擋。
- 快速tests15/15 PASS，報告及SUMMARY/PHASE1/feature audit已保存。舊SOURCE_GATE.json只保留歷史來源證據，其BLOCKED狀態不適用本研究。
- 不讀Forward、不建collector/service/task、不改protected strategies。下一步：本研究結束，未經新指示不硬調參、不重跑baseline。

## V236 D1 Structural Context Gate（2026-09-08，最新任務）

- 使用者明確開啟V236；獨立目錄 V236_D1_STRUCTURE_CONTEXT/。停止V235工作，不修改任何受保護策略、Forward、排程。
- baseline_parity.py 在foreground重跑frozen V188 no-router/full candidate pool。7/7 checks PASS：213/77/36.15023474178404%/3461.5009758019496/PF2.5492243715277993/DD16.9458；243候選identity/order/排倉結果、213成交identity/order、原始decimal geometry一致，16/9 manifests一致，source SHA不變。
- 已完成的baseline不要無故重跑。初版verification誤讀metrics表，修為逐年raw trades；OG 1 ULP為再序列化差異，使用原始raw decimal text exact比較解決；--review-existing只重驗輸出不回測。
- causal_source_gate.py從原始V187/formal simulator/control artifacts檢查出3筆OPEN100%/資料不足卻有舊final_exit_time（ZEST7/30、BTC8/13、ETH8/18）。原V186會使用非空exit時戳；不能證明causal completed outcome，因此不能冒稱D1 counterfactual下的V186動態重算合法。
- 本輪結論 BLOCKED_CAUSAL_SEMANTICS_維持V188；不是D1假設已驗證FAILED。遵守使用者允許causal semantics不能證明即停止；未進Phase1、無Predeclared Rules、无Research winner、無2025 Challenger validation。
- 輸出 V236_REPORT.md / V236_BASELINE_PARITY.json / V236_CAUSAL_SOURCE_GATE.json / V236_CAUSAL_UNRESOLVED.csv 及本次baseline逐筆資料。source gate tests4/4 PASS。
- 下一步須有可靠原始execution event證據或使用者明確新的causal基準治理；不可硬補key、刪樣本、改timestamp或母版繼續D1選參。

## V235 outcome availability timing（2026-09-08，最新）

- 新增 package/v235_outcome_timing.py，分event_bar_time/outcome_known_at。原simulate與V184控制simulator的intrabar SL/TP均標bar.Index，known_at=bar+15m；MA退出明確使用nxt.open/nxt.Index，可知時間為該next open。未知reason拒絕。
- v235_runtime_state.py 的 proposal/reducer 改用經驗證known_at，禁止直接使用歷史closed_at。更新原state fixtures；timing11/11、state8/8 PASS。
- V235_timing_certification.py 開始historical timing gate即發現3筆無法證明close：ZEST 7/30、BTC 8/13、ETH 8/18，均OPEN/remaining100/資料不足，但帶有final_exit_time。原simulate資料不足分支保留輸入舊final_exit_time。不能猜+15m或把它當真close。
- 已輸出 V235_TIMING_REPORT.md / V235_TIMING_CERTIFICATION.json / V235_TIMING_UNRESOLVED.csv。結論 OUTCOME_TIMESTAMP_SEMANTICS_NOT_PROVABLE；新timing16/9尚未certify，full regression因前置gate失敗未執行。不要冒稱timing full parity PASS或已證明所有historical dependency。
- 保持READY=NO、collector/task OFF、start=null。原upstream certification不重跑；正式策略不改、不backfill、不讀V221C。
- 下一步僅追這3筆是否存在真正frozen execution event可證明completed outcome與known_at；無證據則保持fail closed。不要以歷史key或任意timestamp補過。

## V235 upstream control exact certification（2026-09-08，本節優先）

- 使用者已確認：raw → 獨立 frozen V185A/V157-equivalent control portfolio → V186 → 僅 V186_block 時 BTC override → 三模型各自 final allocation。下方 OPPORTUNITY_STREAM_DEFINITION_CONFLICT 已解除。
- 新增 V235_control_stream_certification.py，原 V185.make_candidate + frozen model（不 fit）→ 原 V168.patch_v168/run_variant → V157/V156/V155 → 原 V184.patch_simulator → 正式 allocate_exact/money_management。未自行加入 V162，亦未以版本名稱排除任何控制組行為。
- 實際完整控制組重播 PASS：220 trades / 77 TP1 / +3377.7408U。逐年52/48/120筆；與 frozen V185A 的逐筆 identity/ordering 全相符。另快速讀取雙方結果核對 signal_time/module/entry_mode/entry/stop/TP1/TP2/TP3/逐筆已實現淨利，三年全部完全相同。
- 結果：V235_forward_CONTROL_CERTIFICATION.json；原始輸出隔離在 V235_forward_CONTROL_CERTIFICATION/。這是本次新執行結果，不是將舊CSV加總冒充producer parity。舊CSV僅為比對oracle，不可進Fresh。
- 新增 package/v235_control_portfolio.py：獨立控制組 proposal 使用正式 allocate_exact，MAX5/NEW2、拒絕 raw stage、拒絕三模型feedback、pending不因重啟釋放；仍是整合元件，不是已接通的collector。
- 快速 tests：test_v235_control_stream_certification.py 4/4；test_v235_control_portfolio.py 6/6。既有43/17/8與V188 regression本輪未重跑，不將它們合算成本輪Final E2E PASS。
- 時序review：以原V185A控制組逐筆檢查，若將intrabar stop回饋延至completed time，SQQQ 2026-07-24 13:30 UTC的loss_streak為0，而歷史bar-open口徑為1（兩邊v186_block均False）。Fresh必須用實際available_at，不得因historical close timestamp在bar open而提早讀outcome。這項診斷不是新策略、不是調參。
- 尚未完成：真正fresh transform/data producer接入、V186-kept durable shadow lifecycle、close-only batch journal capture/restart、三模型pending→execution/final allocation、全套Final E2E。collect_completed_batch仍fail closed。不要把控制組歷史parity或6項元件測試稱為Fresh producer已完成。
- READY=NO；collector/task未啟動；start=null。正式V010/V188/FrozenV219/V221C未改；未讀V221C Forward、未回填、未調q66/W2/W4。
- 下一個動作：沿用已驗證控制組stack完成fresh producer接線（不可再跑此220筆certification除非相關程式改動），接入獨立control execution updates及V186-kept completed event。所有proposal必須在durable append成功後才能publish；缺資料整批拒絕。之後才全套readiness/activation。

## V235 opportunity 定義釐清（2026-09-08，最新）

- 只追原始來源，未修改程式、未重跑已通過測試/回測。詳見 V235_OPPORTUNITY_STREAM_CONTRACT_REVIEW.md。
- 最新要求「entry selected gate 後、portfolio allocation 前」若指所有排倉之前，與原 V186 不一致：V187 load_v185a_control_trades 讀 V185A 控制組「全部成交」，該檔由正式 allocate_exact accepted 經 money_management 輸出；derive_v186_blocks 遍歷此成交流。
- 待使用者確認：是否「portfolio allocation 前」指 V188/W2/W4 最終排倉前，允許上游保留獨立原控制組排倉/執行帳本。這可不依賴 V188/W2/W4 outcome，又保持原 opportunity 定義。不得自行把原 V185A 控制執行語意換成 plain V157，也不得引入 V162 額外救援分支。
- 当前 blocker=OPPORTUNITY_STREAM_DEFINITION_CONFLICT（需求語意待釐清，不是既有 tests FAIL）。READY=NO，collector/task 未啟動，start=null；不回填、不讀 V221C Forward。

## V235 Final E2E local certification（2026-09-07，優先於下方舊記錄）

- 本機原環境重跑指定三組：defense 25/25、Fresh runtime 18/18、infra 17/17。執行前後 defense/runtime/builder/journal SHA 不變，package manifest 18/18。
- Fresh builder 已不走 empty-frame，完整資料可因果重算。無已知壓力：原 V155/V84 回傳 +inf，原 V185 make_candidate→feature_vector 將 +inf 編碼為30；Fresh 只在足夠舊高資料且 old_high<=entry 時沿用該已證明的 production 編碼，缺資料仍拒絕。下方「空frame blocker」記錄已過期，不要再重做 builder。
- 最近完整 regression 成功：220/220、16/16、9/9、V188 213/77/3461.5009758019496/PF2.5492243715277993/DD16.9458；來源及 protected 策略未改。
- 新增 package/v235_runtime_state.py 與 回測研究/test_v235_runtime_state.py：8/8 PASS。實作 close-only batch proposal、拒絕 V188 portfolio feedback、拒絕 raw candidate 當 selected stream、append callback 失敗不提交記憶體 state、typed event deterministic reduction、缺事件不重置。
- 這8項是隔離 reducer/transaction callback 測試，不是完整 durable journal/state-recovery 驗收。尚未接入 collector，不可冒稱 runtime 已完成。
- Final E2E gate：實際呼叫 collect_completed_batch() 仍拋 V235_RUNTIME_INPUT_EXECUTION_READINESS_NOT_PASSED。依使用者「任一FAIL停止」保持未啟動。詳見 V235_forward_LOCAL_CERTIFICATION.json。
- 唯一當前 blocker 是尚未完成真實 V157 selected producer→V186-kept shadow event→durable runtime event capture/replay 接線。現有 canonical adapter 只提供候選，不能直接充當 V157 selected；V234 build_stack 會讀歷史固定key，不可用來當 Fresh producer。正式 allocate_exact 位於 V006_1實盤等價回測/run_v006_1_parity.py，可復用，但仍需接上 V157 transform 與獨立 causal shadow state。
- READY=NO、collector/task OFF、start=null；不讀 V221C、不回填。正式 activation、第一批、排程均未執行。

## V235 FIXED package integration（2026-09-07，最新）

- 使用 Downloads/V235_activation_engineering_package_FIXED.zip 覆蓋原 package；manifest 18/18。沒有重寫 journal；原 package 及 FIXED 來源分開保留。
- 兩個上一輪 safety tests 2/2、FIXED infra 17/17 PASS。candidate payload mismatch 與 same-batch duplicate 已修正。
- 新增 package/V235_forward_runtime.py：FreshV185 先驗證原始 merger 實際消耗的 feature，再呼叫 recovered predictor；FreshBTC 使用 completed context 與 package btc_combo2；FreshDefense 在 defense active 時即要求 BTC，整批 proposal 不發佈部分 state。未接通實際 collector entry point。
- 新增 回測研究/test_v235_fresh_runtime.py：10/10 PASS；既有 defense 25/25、preflight offline 16/16、adapter offline 14/14；合計 84/84。syntax 2/2、input SHA 16/16、historical router 19/19 PASS。
- 本輪已重新完成 `py -B 回測研究/V235_forward_defense_recovery.py --full`：220/220、16/16、9/9；完整 V188 213 trades / 77 TP1 / Net 3461.5009758019496 / PF 2.5492243715277993 / DD 16.9458 全對上。未受後續策略修改影響，不要無故再跑。
- Blocker：原 FrozenV185 在非 LONG V85/V96 分支刻意傳空 frame，重算 volume/slope/score 為 NaN，historical feature_vector 使用預設值。Fresh no-default gate 正確拒絕；不得擅自把空 frame 改為完整行情，否則改了 frozen prediction。可用 `test_legacy_empty_frame_cannot_bypass_completeness` 重現，資料為合成完整 K，非四筆 expired fixture。
- `V235_forward_RUNTIME_READINESS.json` 保存完整測試、SHA、未完成項目。READY=NO、collector/task OFF、start=null；沒有 activation、live smoke、live parity 重跑、backfill 或讀 V221C Forward。正式受保護程式不改。
- 下一步只解決 frozen feature producer 與 fresh completeness 的兼容性；不得猜值、改 frozen 路由或把拒絕當無訊號。然後才續接 full input/allocator/獨立 V186 shadow feedback/三模型 execution 與 restart recovery。runtime 的 collect_completed_batch 仍明確 fail closed，不可宣稱完整 collector 已整合。

## 最新 V235（2026-09-06）

- 歷史 Robustness 已實作與執行；總結 `PROMISING_NOT_PASSED`，Fresh `WAIT_MORE_DATA`。正式 V010、V188、Frozen V219、V221C 與 q66/W2/W4 全部未改。
- 程式 `V235_robustness.py`；先登記 `V235_PROTOCOL.json`：種子 235202609、5,000 次年度內配對進場月份區塊抽樣、95% interval、分組至少30筆才可描述、Fresh最低50/理想100筆。禁止依結果改規格。
- 三年 Net 勝過 control 的重抽樣比例：W2 66.10%，W4 93.34%；delta 95% 區間：W2 [-316.10,+531.98]U，W4 [-75.05,+737.22]U。兩者跨0，不宣稱獨立 edge 確認。
- Research W2/W4 相同：比例96.52%，區間[-6.48,+253.02]U。Validation W2 47.40%、W4 83.84%；不因這段選 W4。
- W2 去 Top1 delta 後 -42.256533U；W4 +152.321924U。W2 最大改善為新增 TA +133.176928U，Control 拒絕原因為 LONG 方向停損冷卻。沒有 parent-router→slot→new-trade 完整事件鏈，標 `INSUFFICIENT_EVENT_LINEAGE`，不能稱已完成因果排倉穩健驗收。
- Binance 209/197/198筆，Bitget三版本各3筆，BingX各1筆；SHORT各25筆。小樣本標 INSUFFICIENT_SAMPLE。
- Bootstrap 是 fixed realized-U entry-cohort monthly block，不重新計算複利、風險、排倉或 runner；因此不產生虛假的 bootstrap chronological DD。既有年度DD原口徑另保留在 METRICS。
- Fresh audit：舊 Research/Validation已看過；2026-08-19以後無可驗證 untouched manifest。沒有讀 V221C ledger，沒有 Fresh 回測成交。不能把這個有限來源稽核宣稱搜尋過所有可能的新資料。
- `V235_FORWARD_CONTRACT.json` 已凍結獨立新起點規則，但 collector 未啟動、FORWARD_COLLECTION_STARTED_AT=null。第一根合法完成事件成功 append 後才可設定真起點；不回填、不假造啟動時間。
- 輸出：REPORT、SUMMARY、INPUT_LOCK、METRICS、SPLITS、TAIL_ALPHA、DEPENDENCE、BOOTSTRAP、SCHEDULING_ATTRIBUTION、RECONCILIATION、FRESH_DATA_AUDIT；均在回測研究直接放置。
- 成功：`py -m py_compile 回測研究/V235_robustness.py 回測研究/test_v235_robustness.py`；`py 回測研究/test_v235_robustness.py` 10/10 PASS；`py 回測研究/V235_robustness.py` 完成並重跑，各CSV/JSON/報告SHA一致。既有V234輸入SHA前後一致，reconciliation誤差<1e-8。
- Reviewer 修正：2025標籤包含2026年8月，不能按曆年/8月機械切成不存在2026 stratum；配對source ID若延後跨月仍使用同一control cohort，新增訊號才用自己的月份。
- 下一步：取得可稽核 untouched 新資料或另建獨立 V235 collector，確認 frozen source與完整事件/排倉記錄後啟動。需要此新增工作才能完成 Fresh Validation；不要重跑V234三年，不改 frozen 規則，不從2025選W4。

日期：2026-09-06。以本節及 V234_SUMMARY.json 為最新狀態，根目錄舊 V233E 記錄是歷史。

## 已完成

- V234 = **PROMISING_NOT_PASSED**；V188 維持 research baseline。
- 目標程式：V234_full_market_ChatGPT.py。只改 retention、dependence、status gate、Markdown 與報告更新入口。
- `--review-only` 只從既有 CSV 更新 SUMMARY / REPORT，不載入市場引擎、不重跑回測。
- Trade Retention 改為 source ID 交集：W2 196/213=92.018779%；W4 198/213=92.957746%。新增交易不列入保留。
- W2 Top1 remaining advantage=-42.256533U，Dependence PASS=False，因此不能 PASSED_RESEARCH。
- W4 Top1 remaining advantage=+152.321924U，但不能用 2025 較好選 W4。Research 2023+2024 同為 +102.179U，RESEARCH_EQUIVALENT。
- V234_SUMMARY.json 的原始 source_sha256 完整保留；reviewer_patch.code_sha256 另記新版程式 SHA-256。這不是新一輪回測來源。
- 原執行目標程式 SHA-256：3a219b2e1a96f8c94fb76a17f503d9a098569c098b71cb74e4ccb1d34b22ceb7。
- V010 / V188 / Frozen V219 / V221C、q66 / windows / reclaim / stop / TP / risk / fees / runner 全部未動。

## 保留績效

| 版本 | Trades | TP1 | Net U | PF | DD % |
|---|---:|---:|---:|---:|---:|
| V188 | 213 | 77 | 3461.5009758019496 | 2.5492243715277993 | 16.9458 |
| W2 Full | 201 | 76 | 3552.4213708289203 | 2.748879691197751 | 15.1639 |
| W4 Full | 202 | 77 | 3753.002368387184 | 2.8137073842780724 | 15.1639 |

DD 仍是原正式年度 reset 口徑；本次不改為連續三年 equity。YEARLY 的既有勝率與 TOTAL TP1/Trades 分母不同，原始 CSV 保留，勿混比。

## 成功指令

- `py -m py_compile 回測研究/V234_full_market_ChatGPT.py 回測研究/test_v234_reviewer_patch.py`
- `py 回測研究/test_v234_reviewer_patch.py`：9/9 PASS。覆蓋 retention 交集、新增交易、Top1 正負與零、缺漏／重複／非有限值、W4-only 不升級、variant_summary、報告無 tabulate、禁止回測呼叫、原來源 SHA 保留、所有 V234 CSV SHA 不变與六項績效不變。
- 單純更新報告：`py 回測研究/V234_full_market_ChatGPT.py --review-only`。不要在本輪無參數執行 main（會重跑三年）。

## 現場與下一步

- 原本 `回測研究/HANDOFF.md` 不存在，因此本次建立；根目錄 HANDOFF.md 同步索引。
- 上一輪 V234_full_market.py 的 Control 嘗試因 Windows 暫存目錄權限失敗，已被使用者提供的完成版 V234_full_market_ChatGPT.py 與結果取代；不要續跑舊嘗試，也不刪除既有檔案。
- 下一步 = **V235 Robustness / Fresh Validation**。目前只完成 V235_PLAN.md，不啟動回測、不調參、不選 W4。
- V235 第一個動作：讀取 V235_PLAN.md，確認新資料是否真的 untouched 與驗收規格；凍結所有方案後才執行。不得使用 V221C Forward OOS tuning。
