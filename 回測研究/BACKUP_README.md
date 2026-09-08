# 2026-09-08 公開備份

- 正式雷達：由雲端 radar.service 的實際 PID/cmdline 核對為 radar_v010.py。41 個 Python 原始檔是雲端下載的靜態依賴閉包，SHA 見正式雷達/LIVE_SOURCE_PROVENANCE.json；不是本機舊副本，也不代表所有保守收集的模組均已載入記憶體。
- Frozen_V188：原始來源 SHA、固定16-key/9-block歷史研究契約、213成交與基準 parity 證據。來源未改寫；重跑仍需要原本資料路徑及私人行情資料。本備份不是含憑證及行情庫的一鍵實盤部署包。
- V238_COMPRESSION_EXPANSION：Phase 1 完整觀察、Top1/3/5穩健性、因果測試及 FAILED 結論。未建立 Challenger，未用2025 outcome選參，未開V239。
- HANDOFF.md：保留交接歷史，最新結論在最上方。
- 所有公開新增檔案經副檔名白名單、憑證特徵及 Python literal AST 掃描。未上傳 .env、log、state、cache 或任何私人行情快取；掃描不是對所有未知秘密格式的數學保證。
- 既有 OSS 範例與歷史保留，與本次正式雷達／研究來源快照明確分開。本次備份不會部署或重啟雲端服務。
