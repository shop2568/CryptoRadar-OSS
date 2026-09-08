#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V156 高信心敗單防護｜三年回測

研究基準：V155 敗單防護重算版。

只修改敗單防護的觸發強度：
1. V85 必須在「上方空間不足」之外，同時出現至少 3 個弱點才排除。
2. V96 必須同時滿足「上方空間低於 5R」及「進場前 8 根漲幅超過 2.5%」才排除。

不修改訊號來源、結構停損、最低 4R、TP1/TP2/TP3、排倉、最多 5 倉、
單筆風險、成本保護或 5/10 日均線出場。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pandas as pd


SOURCE = Path.home() / "Downloads" / "backtest_1y_v155_failure_guard_recalc_3y.py"
WORK = Path.home() / "Documents" / "ChatGPT" / "回測專案"
OUT = WORK / "V006_1實盤等價回測" / "V156_高信心敗單防護_三年結果"
BASELINE = (
    Path.home()
    / "Desktop"
    / "回測專案"
    / "V006_1實盤等價回測"
    / "V155_敗單防護_三年結果"
    / "V155_三年結果.csv"
)


def load_v155():
    if not SOURCE.exists():
        raise FileNotFoundError(f"找不到 V155 基準程式：{SOURCE}")
    spec = importlib.util.spec_from_file_location("v155_recalc", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("無法載入 V155 基準程式")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def promote_outputs() -> dict:
    mapping = {
        "V155_三年結果.csv": "V156_三年結果.csv",
        "V155_規則生效統計.csv": "V156_規則生效統計.csv",
        "V155_跨年比較.csv": "V156_跨年比較_引擎原始.csv",
        "V155_最終摘要.json": "V156_引擎原始摘要.json",
    }
    for old, new in mapping.items():
        src = OUT / old
        if src.exists():
            shutil.copy2(src, OUT / new)

    result_path = OUT / "V156_三年結果.csv"
    current = pd.read_csv(result_path)
    baseline = pd.read_csv(BASELINE) if BASELINE.exists() else pd.DataFrame()

    current_total = float(current["已實現淨利_U"].sum())
    current_trades = int(current["交易數"].sum())
    current_tp1 = int(current["TP1筆數"].sum())
    current_stops = int(current_trades - current_tp1)
    current_wr = 100.0 * current_tp1 / current_trades if current_trades else 0.0
    current_max_dd = float(current["最大回撤_pct"].max())

    if not baseline.empty:
        baseline_total = float(baseline["已實現淨利_U"].sum())
        baseline_trades = int(baseline["交易數"].sum())
        baseline_tp1 = int(baseline["TP1筆數"].sum())
        baseline_wr = 100.0 * baseline_tp1 / baseline_trades if baseline_trades else 0.0
        baseline_max_dd = float(baseline["最大回撤_pct"].max())
    else:
        baseline_total = baseline_trades = baseline_tp1 = baseline_wr = baseline_max_dd = 0.0

    comparison = pd.DataFrame(
        [
            {
                "版本": "V155 敗單防護",
                "交易數": baseline_trades,
                "TP1筆數": baseline_tp1,
                "總勝率_pct": round(baseline_wr, 4),
                "三年淨利_U": round(baseline_total, 4),
                "最大年度回撤_pct": round(baseline_max_dd, 4),
            },
            {
                "版本": "V156 高信心敗單防護",
                "交易數": current_trades,
                "TP1筆數": current_tp1,
                "總勝率_pct": round(current_wr, 4),
                "三年淨利_U": round(current_total, 4),
                "最大年度回撤_pct": round(current_max_dd, 4),
            },
        ]
    )
    comparison.to_csv(OUT / "V156_V155比較.csv", index=False, encoding="utf-8-sig")

    report = {
        "版本": "V156 高信心敗單防護",
        "研究基準": "V155 敗單防護重算版",
        "V155來源程式": str(SOURCE),
        "V155來源SHA256": sha256(SOURCE),
        "固定修改": {
            "V85額外弱點最低數": 3,
            "V96上方空間門檻_R": 5.0,
            "V96進場前8根漲幅門檻_pct": 2.5,
        },
        "未修改": [
            "訊號來源",
            "結構停損",
            "最低4R",
            "TP1/TP2/TP3",
            "排倉與最多5倉",
            "單筆風險與資金曲線",
            "成本保護與5/10日均線出場",
        ],
        "V156三年": {
            "交易數": current_trades,
            "TP1筆數": current_tp1,
            "停損及其他未命中TP1": current_stops,
            "總勝率_pct": round(current_wr, 4),
            "已實現淨利_U": round(current_total, 4),
            "最大年度回撤_pct": round(current_max_dd, 4),
        },
        "相對V155": {
            "交易數變化": current_trades - baseline_trades,
            "TP1筆數變化": current_tp1 - baseline_tp1,
            "勝率變化_百分點": round(current_wr - baseline_wr, 4),
            "淨利變化_U": round(current_total - baseline_total, 4),
            "最大年度回撤變化_百分點": round(current_max_dd - baseline_max_dd, 4),
        },
        "逐年結果": current.to_dict(orient="records"),
    }
    (OUT / "V156_最終報告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> int:
    module = load_v155()
    module.OUT = OUT

    # V156 固定假設；不做網格搜尋，不依結果回頭挑門檻。
    module.V155_V85_BAD_CONFIRMATIONS = 3
    module.V155_V96_MIN_ROOM_R = 5.0
    module.V155_V96_MAX_RET8 = 0.025

    print("V156 高信心敗單防護開始；V155 為控制組。")
    print("只降低誤殺，不修改交易策略其他部分。")
    code = int(module.main())
    if code != 0:
        return code

    report = promote_outputs()
    print("\nV156 與 V155 比較：")
    print(pd.read_csv(OUT / "V156_V155比較.csv").to_string(index=False))
    print(f"\n輸出資料夾：{OUT}")
    print(f"淨利變化：{report['相對V155']['淨利變化_U']:+.2f} U")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
