#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V154 結構進場｜三年正式重排倉
====================================

目的：
把「停損不要貼太近；要放在進場前已確認的前低/前高外面，
如果合理結構停損太寬或讓 TP1 不足 4R，就不要進」做成可回測版本。

V153 = V150 ELITE 基礎 + 結構停損風險調整

B 固定規則（不依年份調參）：
1. 所有訊號原始 TP1 RR 先 >= 4R。
2. V72.8S LONG 維持 OFF；V72.8S SHORT 不改停損。
3. V85 LONG：
   - ema20_slope8_atr <= 0.60（保留 V150）
   - 進場前 24 根 15m 內找「最近已確認 swing low」
   - swing low 下方 0.15 ATR 為結構停損
   - 只允許把原停損「放寬」到結構外，不會為了回測好看把停損縮緊
   - 最終 stop_pct <= 2.0%；否則不進
   - 放寬後沿用原 TP1；若實際 RR < 4R，不進
4. V96 LONG：
   - 同樣找最近已確認 swing low，下面 0.15 ATR
   - 最終 stop_pct <= 3.5%；否則不進
   - 沿用原 TP1；放寬後不足 4R，不進
5. V88 / V72 SHORT / V139 不加結構停損。
6. 不改 V006.1 parity：
   24h cooldown / MAX5 / NEW2 / 同方向風控 /
   1% realized-equity risk / 50-25-25 / 成本保本 / 5-10日線。

「已確認 swing」定義：
pivot 左右各 2 根已完成 K；所有資料都必須早於 entry_time。
不使用進場後 K 線，因此是 causal。

這是 研究影子/研究版；不修改正式雷達。\nV153重點：停損可放寬，但帳戶單筆風險仍維持1%；若合理結構SL太遠或不足4R，直接不進。
"""


# 版本治理：沿用研究回測版本序列 V149 → V150 → V151 → V152 → V153 → V154 → V155。
# 這是研究回測版本，不代表正式雷達版本。

# 研究版序列：V149=adaptive stop, V150=ELITE, V151=STRUCTURE, V152=HYBRID, V153=STRUCTURE-RISK, V154=STRUCTURE-ENTRY, V155=FAILURE-GUARD

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import math
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------
# 路徑 / 固定規則
# ---------------------------------------------------------------------

HOME = Path.home()
CRYPTO = HOME / "CryptoRadar"
WORK = HOME / "Desktop" / "回測專案"
if not WORK.exists():
    WORK = HOME / "Documents" / "ChatGPT" / "回測專案"

HERE = WORK / "V006_1實盤等價回測"
PARITY_PATH = HERE / "run_v006_1_parity.py"
OUT = HERE / "V155_敗單防護_三年結果"

CACHE_2023 = (
    CRYPTO
    / "backtest_v006_1_original_2023_upstream_results"
    / "historical_cache_15m"
)

PERIOD_CONFIG = {
    "2023": {
        "candidate": [
            CRYPTO / "backtest_v006_1_original_2023_upstream_results" / "15_v139_final" / "V139_A全部交易.csv",
            CRYPTO / "backtest_v006_1_original_2023_upstream_results" / "15_v139_final" / "V139_A組合風控未選中.csv",
        ],
        "quality": CRYPTO / "backtest_v006_1_original_2023_upstream_results" / "01_v52_rr4" / "exchange_markets" / "data_quality.csv",
        "source_note": "V155 敗單防護｜2023 ORIGINAL同版上游V139_A",
    },
    "2024": {
        "candidate": [
            CRYPTO / "backtest_v145_previous_year_upstream_rebuild_results" / "15_v139_final" / "V139_A全部交易.csv",
            CRYPTO / "backtest_v145_previous_year_upstream_rebuild_results" / "15_v139_final" / "V139_A組合風控未選中.csv",
        ],
        "quality": CRYPTO / "backtest_v145_previous_year_upstream_rebuild_results" / "01_v52_rr4" / "exchange_markets" / "data_quality.csv",
        "source_note": "V155 敗單防護｜2024 V145正確重建V139_A",
    },
    "2025": {
        "candidate": [
            WORK / "backtest_1y_v139_narrow_overbuild_hard_exit_results" / "report_candidate_all_trades.csv",
            WORK / "backtest_1y_v139_narrow_overbuild_hard_exit_results" / "portfolio_rejected.csv",
        ],
        "quality": CRYPTO / "backtest_1y_v52_results" / "exchange_markets" / "data_quality.csv",
        "source_note": "V155 敗單防護｜2025 +3444 ORIGINAL V139_A",
    },
}

KNOWN_ORIGINAL = {
    "2023": {"pnl": -78.6227, "trades": 101},
    "2024": {"pnl": -366.7706, "trades": 121},
    "2025": {"pnl": 3444.1517, "trades": 160},
}

# 使用者剛完成的 V150 ELITE 三年結果，作 A/B 對照。
KNOWN_V150 = {
    "2023": {"pnl": -159.1008, "trades": 52},
    "2024": {"pnl": 64.3753, "trades": 48},
    "2025": {"pnl": 3069.1624, "trades": 119},
}

KNOWN_V151_STRUCTURE = {
    "2023": {"pnl": 6.1911, "trades": 31},
    "2024": {"pnl": 26.1801, "trades": 31},
    "2025": {"pnl": 1522.8641, "trades": 82},
}


# 使用者已完成的 V154 結構進場 三年結果。
KNOWN_V154 = {
    "2023": {"pnl": -159.0981, "trades": 52},
    "2024": {"pnl": 64.3601, "trades": 48},
    "2025": {"pnl": 3110.1785, "trades": 118},
}

# ---------------------------------------------------------------------
# V155 敗單防護 固定研究規則
# ---------------------------------------------------------------------
#
# 原則：
# - 不拿進場後資料決定「要不要進」。
# - 只用 entry_time 之前就存在的候選特徵做無動能硬閘門。
# - 缺欄位時不亂猜、不砍單。
# - 門檻使用前面三年/模組分析得到的「粗整數區間」，不是針對單年最佳化。
#
# V85：只有「上方空間不足」同時再出現至少兩個弱點才砍。
V155_V85_MIN_ROOM_R = 10.0
V155_V85_MAX_RANGE_ATR = 4.5
V155_V85_MAX_RET8 = 0.015       # 1.5%
V155_V85_MIN_SCORE = 45.0
V155_WEAK_VOLUME_RATIO = 1.8
V155_WEAK_VOLUME_BUILD = 1.6
V155_V85_BAD_CONFIRMATIONS = 2

# V96：先抓最明顯的「空間小 + 已經漲太多」組合。
V155_V96_MIN_ROOM_R = 6.0
V155_V96_MAX_RET8 = 0.015       # 1.5%

# 進場後 1/2/3 小時只做 研究影子 診斷，不影響本版績效。
V155_DIAG_HORIZONS = (4, 8, 12)  # 15m bars

MIN_RR = 4.0

# V150 已驗證過的 V85 趨勢過熱門。
V85_MAX_EMA20_SLOPE_ATR = 0.60

# V154 結構進場 固定規則（不依年份調參）
STRUCT_LOOKBACK_BARS = 24      # 最近 6 小時找已確認結構
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# 結構停損放在前低外一點；固定研究值，待三年結果驗證。
STRUCTURE_BUFFER_ATR = 0.15

# 三年全量分析：真正「稍微放寬就能救」主要落在 <=30% 風險增加。
MAX_RISK_GROWTH = 0.30

# 現在 Entry 太高時，不把 TP 往外推；反算可接受 Entry 後最多等 3 小時回踩。
REPRICE_WAIT_BARS = 12

# V150 原始 V85 品質門仍保留。
V85_ORIGINAL_MAX_STOP_PCT = 0.020

# 合理結構 SL 的最終距離上限。
V85_FINAL_MAX_STOP_PCT = 0.025
V96_FINAL_MAX_STOP_PCT = 0.040

TARGET_STRUCTURE_MODULES = ("V85", "V96")

FRAME_CACHE: dict[tuple[str, str, str], pd.DataFrame] = {}


# ---------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------

def number(value: Any, default: float = math.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def side(row: dict) -> str:
    a = str(row.get("direction") or "").upper()
    if a in {"LONG", "SHORT"}:
        return a
    b = str(row.get("direction_v92") or "").upper()
    return b if b in {"LONG", "SHORT"} else ""


def module_name(row: dict) -> str:
    return str(
        row.get("module")
        or row.get("setup")
        or row.get("setup_type")
        or row.get("candidate_family")
        or ""
    )


def price_rr(row: dict, stop_override: float | None = None) -> float:
    entry = number(row.get("entry"))
    stop = number(row.get("stop")) if stop_override is None else number(stop_override)
    tp1 = number(row.get("tp1"))
    direction = side(row)
    risk = abs(entry - stop)

    if not all(math.isfinite(x) for x in (entry, stop, tp1, risk)):
        return math.nan
    if entry <= 0 or stop <= 0 or tp1 <= 0 or risk <= 0:
        return math.nan

    if direction == "LONG":
        return (tp1 - entry) / risk
    if direction == "SHORT":
        return (entry - tp1) / risk
    return math.nan


def stop_pct(row: dict, stop_override: float | None = None) -> float:
    entry = number(row.get("entry"))
    stop = number(row.get("stop")) if stop_override is None else number(stop_override)
    if not math.isfinite(entry) or not math.isfinite(stop) or min(entry, stop) <= 0:
        return math.nan
    return abs(entry - stop) / entry



def first_number(row: dict, keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in row:
            x = number(row.get(key))
            if math.isfinite(x):
                return x
    return math.nan


def v155_recalculate_features(row: dict, frame: pd.DataFrame) -> dict:
    """
    只使用 entry_time 之前已完成的 15m K 線重新計算。
    不依賴候選 CSV 是否有 volume/room/ret8/range 等欄位。

    計算語意對齊 V84/V85 原始因果特徵：
    - 成交量比：訊號K成交量 / 前20根成交量中位數
    - 量能堆積：最近3根平均量 / 前20根成交量中位數
    - 20根區間/ATR：訊號K之前20根高低區間 / 訊號K ATR14
    - EMA20 8根斜率/ATR
    - 進場前8根報酬
    - 上方空間：30日舊高（排除最近8小時）相對原始風險距離
    """
    out = {
        "recalc_ok": False,
        "missing_reason": "",
        "score": math.nan,
        "volume_ratio": math.nan,
        "volume_build": math.nan,
        "ema20_slope8_atr": math.nan,
        "ret8": math.nan,
        "range_atr": math.nan,
        "room_r": math.nan,
        "structure_distance_atr": math.nan,
        "signal_close_location": math.nan,
        "atr": math.nan,
        "signal_time": None,
    }

    if frame is None or frame.empty:
        out["missing_reason"] = "缺少K線"
        return out

    t = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")
    if pd.isna(t):
        out["missing_reason"] = "進場時間無效"
        return out

    hist = frame[frame.index < t].copy()
    if len(hist) < 32:
        out["missing_reason"] = f"進場前K線不足32根：只有{len(hist)}根"
        return out

    # 嚴格只用進場前最後一根已完成K作為訊號K。
    signal = hist.iloc[-1]
    out["signal_time"] = hist.index[-1]

    high = pd.to_numeric(hist["high"], errors="coerce")
    low = pd.to_numeric(hist["low"], errors="coerce")
    close = pd.to_numeric(hist["close"], errors="coerce")
    open_ = pd.to_numeric(hist["open"], errors="coerce")
    volume = pd.to_numeric(hist["volume"], errors="coerce")

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_series = tr.rolling(14, min_periods=14).mean()
    atr = number(atr_series.iloc[-1])
    out["atr"] = atr
    if not math.isfinite(atr) or atr <= 0:
        out["missing_reason"] = "ATR無法計算"
        return out

    # EMA20 與 8 根斜率
    ema20 = close.ewm(span=20, adjust=False).mean()
    if len(ema20) >= 9:
        out["ema20_slope8_atr"] = number(
            (ema20.iloc[-1] - ema20.iloc[-9]) / atr
        )

    # 進場前8根漲幅
    if len(close) >= 9 and number(close.iloc[-9]) > 0:
        out["ret8"] = number(close.iloc[-1] / close.iloc[-9] - 1.0)

    # 成交量比與量能堆積，對齊原本 V84/V85：
    # current volume / prior 20 median
    # last 3 mean / prior 20 median
    prior20_vol = volume.iloc[-21:-1]
    vol_median = number(prior20_vol.median())
    if math.isfinite(vol_median) and vol_median > 0:
        out["volume_ratio"] = number(volume.iloc[-1] / vol_median)
        out["volume_build"] = number(volume.iloc[-3:].mean() / vol_median)

    # 對齊 range20_atr：訊號K「之前」20根，不含訊號K。
    prior20 = hist.iloc[-21:-1]
    if len(prior20) == 20:
        p_high = number(pd.to_numeric(prior20["high"], errors="coerce").max())
        p_low = number(pd.to_numeric(prior20["low"], errors="coerce").min())
        if math.isfinite(p_high) and math.isfinite(p_low):
            out["range_atr"] = number((p_high - p_low) / atr)

    # 訊號K收盤位置，用於重算 V84/V85 原始 score。
    sig_high = number(signal.get("high"))
    sig_low = number(signal.get("low"))
    sig_close = number(signal.get("close"))
    span = sig_high - sig_low
    if math.isfinite(span) and span > 0:
        out["signal_close_location"] = number((sig_close - sig_low) / span)

    # 上方已知舊壓力：對齊 old_high30d，排除最近32根=8小時。
    # 至少需要7天歷史才使用，避免歷史不足時亂算。
    old_pool = high.iloc[:-32].tail(96 * 30)
    entry = number(row.get("entry"))
    stop = number(row.get("stop"))
    risk = abs(entry - stop)

    if len(old_pool) >= 96 * 7 and entry > 0 and risk > 0:
        old_high = number(old_pool.max())
        if math.isfinite(old_high):
            if old_high <= entry:
                out["room_r"] = float("inf")
            else:
                out["room_r"] = number((old_high - entry) / risk)

    # V85 原始候選分數：只有 entry_mode 可辨識時才重算，
    # 不知道模式就留空，不用猜。
    mode = str(row.get("entry_mode") or "").strip()
    base_score = math.nan
    if "壓縮早突破" in mode:
        base_score = 18.0
    elif "均線假跌破站回" in mode:
        base_score = 14.0

    if (
        math.isfinite(base_score)
        and math.isfinite(out["volume_build"])
        and math.isfinite(out["volume_ratio"])
        and math.isfinite(out["ema20_slope8_atr"])
        and math.isfinite(out["signal_close_location"])
        and math.isfinite(out["range_atr"])
        and math.isfinite(out["ret8"])
    ):
        out["score"] = number(
            base_score
            + min(out["volume_build"], 3.0) * 8.0
            + min(out["volume_ratio"], 3.0) * 4.0
            + min(max(out["ema20_slope8_atr"], 0.0), 2.0) * 4.0
            + out["signal_close_location"] * 5.0
            - max(out["range_atr"] - 3.0, 0.0) * 2.0
            - max(out["ret8"] - 0.025, 0.0) * 100.0
        )

    # 結構位置只做診斷。
    direction = side(row)
    pivot, _ = recent_confirmed_pivot(frame, t, direction)
    if math.isfinite(pivot) and entry > 0:
        if direction == "LONG":
            out["structure_distance_atr"] = number((entry - pivot) / atr)
        elif direction == "SHORT":
            out["structure_distance_atr"] = number((pivot - entry) / atr)

    # 關鍵值齊全才視為「重新計算成功」。
    module = module_name(row).upper()
    if "V85" in module:
        required = (
            out["volume_ratio"],
            out["volume_build"],
            out["ema20_slope8_atr"],
            out["ret8"],
            out["range_atr"],
            out["room_r"],
        )
    elif "V96" in module:
        required = (out["ret8"], out["room_r"])
    else:
        required = ()

    if all(math.isfinite(x) or x == float("inf") for x in required):
        out["recalc_ok"] = True
    else:
        missing = []
        labels = {
            "volume_ratio": "成交量比",
            "volume_build": "量能堆積",
            "ema20_slope8_atr": "均線斜率",
            "ret8": "進場前8根漲幅",
            "range_atr": "20根區間",
            "room_r": "上方空間",
        }
        for key, label in labels.items():
            val = out[key]
            if key in (
                "volume_ratio",
                "volume_build",
                "ema20_slope8_atr",
                "ret8",
                "range_atr",
                "room_r",
            ):
                if "V96" in module and key not in ("ret8", "room_r"):
                    continue
                if not (math.isfinite(val) or val == float("inf")):
                    missing.append(label)
        out["missing_reason"] = "、".join(missing) if missing else "資料不足"

    return out


def v155_failure_guard(
    row: dict,
    frame: pd.DataFrame,
) -> tuple[bool, str, dict]:
    """
    V155 無動能進場硬閘門。
    特徵全部由進場前15m K重新計算，不再依賴候選CSV欄位。
    """
    module = module_name(row).upper()
    direction = side(row)
    f = v155_recalculate_features(row, frame)

    detail = {
        "V155_recalc_ok": f["recalc_ok"],
        "V155_missing_reason": f["missing_reason"],
        "V155_score": f["score"],
        "V155_volume_ratio": f["volume_ratio"],
        "V155_volume_build": f["volume_build"],
        "V155_ema20_slope8_atr": f["ema20_slope8_atr"],
        "V155_ret8": f["ret8"],
        "V155_range_atr": f["range_atr"],
        "V155_room_r": f["room_r"],
        "V155_structure_distance_atr": f["structure_distance_atr"],
        "V155_signal_close_location": f["signal_close_location"],
        "V155_atr": f["atr"],
        "V155_signal_time": f["signal_time"],
        "V155_bad_count": 0,
        "V155_bad_flags": "",
    }

    if direction != "LONG":
        return True, "非做多訊號，不加無動能門", detail

    if not ("V85" in module or "V96" in module):
        return True, "非V85/V96，不加無動能門", detail

    if not f["recalc_ok"]:
        return True, f"重新計算資料不足，先放行：{f['missing_reason']}", detail

    # V85：上方空間不足是必要條件，再要求至少兩個弱點同時成立。
    if "V85" in module:
        room = f["room_r"]
        if room == float("inf") or room >= V155_V85_MIN_ROOM_R:
            return True, "V85上方空間充足", detail

        bad = []

        if f["range_atr"] > V155_V85_MAX_RANGE_ATR:
            bad.append("區間過熱")

        if f["ret8"] > V155_V85_MAX_RET8:
            bad.append("進場前漲幅過高")

        if math.isfinite(f["score"]) and f["score"] < V155_V85_MIN_SCORE:
            bad.append("候選分數偏低")

        if (
            f["volume_ratio"] < V155_WEAK_VOLUME_RATIO
            and f["volume_build"] < V155_WEAK_VOLUME_BUILD
        ):
            bad.append("量能偏弱")

        detail["V155_bad_count"] = len(bad)
        detail["V155_bad_flags"] = "、".join(bad)

        if len(bad) >= V155_V85_BAD_CONFIRMATIONS:
            return (
                False,
                f"V85無動能：上方空間僅{room:.2f}R，且同時出現{len(bad)}個弱點：{'、'.join(bad)}",
                detail,
            )

        return True, "V85上方空間偏小，但弱點不足兩個", detail

    # V96：只抓最明顯的「上方空間小 + 已經漲太多」。
    if "V96" in module:
        room = f["room_r"]
        ret8 = f["ret8"]

        if (
            room != float("inf")
            and room < V155_V96_MIN_ROOM_R
            and ret8 > V155_V96_MAX_RET8
        ):
            detail["V155_bad_count"] = 2
            detail["V155_bad_flags"] = "上方空間不足、進場前漲幅過高"
            return (
                False,
                f"V96無動能：上方空間{room:.2f}R、進場前8根漲幅{ret8*100:.2f}%",
                detail,
            )

        return True, "V96未出現低空間加過熱組合", detail

    return True, "通過", detail


def v155_entry_validity_guard(
    row: dict,
    frame: pd.DataFrame,
) -> tuple[bool, str, dict]:
    """
    HIVE 類型保護：
    只檢查實際準備成交那根15m K的開盤是否已越過停損。
    這是當下即可知道的資訊，不使用該根未來高低。
    """
    detail = {
        "V155_entry_open": math.nan,
        "V155_entry_stop_invalid": False,
    }

    if frame is None or frame.empty:
        return True, "缺少K線，無法做進場有效性檢查", detail

    t = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")
    direction = side(row)
    stop = number(row.get("stop"))

    if pd.isna(t) or direction not in {"LONG", "SHORT"} or not math.isfinite(stop):
        return True, "進場有效性資料不足", detail

    pos = int(frame.index.searchsorted(t, side="left"))
    if pos < 0 or pos >= len(frame):
        return True, "找不到進場K線", detail

    bar = frame.iloc[pos]
    op = number(bar.get("open"))
    detail["V155_entry_open"] = op

    if not math.isfinite(op):
        return True, "進場K線開盤價缺失", detail

    invalid = (
        (direction == "LONG" and op <= stop)
        or (direction == "SHORT" and op >= stop)
    )
    detail["V155_entry_stop_invalid"] = bool(invalid)

    if invalid:
        return False, "進場當下停損已失效，取消進場", detail

    return True, "進場當下停損有效", detail


def v155_no_progress_diagnostic(row: dict, frame: pd.DataFrame) -> dict:
    """
    進場後診斷，只輸出數據，不影響 V155 成交/出場。
    目的是下一輪判斷 MORPHO 類型是否值得加 time/無進展 exit。
    """
    out = {
        "V155_diag_only": True,
        "V155_same_bar_stop": False,
        "V155_entry_bar_open_beyond_stop": False,
    }

    if frame is None or frame.empty:
        return out

    direction = side(row)
    entry = number(row.get("entry"))
    stop = number(row.get("stop"))
    t = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")
    risk = abs(entry - stop)

    if (
        direction not in {"LONG", "SHORT"}
        or pd.isna(t)
        or not math.isfinite(entry)
        or not math.isfinite(stop)
        or risk <= 0
    ):
        return out

    bars = frame[frame.index >= t].head(max(V155_DIAG_HORIZONS) + 1)
    if bars.empty:
        return out

    first = bars.iloc[0]
    op = number(first.get("open"))
    hi0 = number(first.get("high"))
    lo0 = number(first.get("low"))

    if direction == "LONG":
        out["V155_same_bar_stop"] = bool(math.isfinite(lo0) and lo0 <= stop)
        out["V155_entry_bar_open_beyond_stop"] = bool(math.isfinite(op) and op <= stop)
    else:
        out["V155_same_bar_stop"] = bool(math.isfinite(hi0) and hi0 >= stop)
        out["V155_entry_bar_open_beyond_stop"] = bool(math.isfinite(op) and op >= stop)

    for n in V155_DIAG_HORIZONS:
        seg = bars.head(n + 1)
        if len(seg) == 0:
            continue

        highs = pd.to_numeric(seg["high"], errors="coerce")
        lows = pd.to_numeric(seg["low"], errors="coerce")
        closes = pd.to_numeric(seg["close"], errors="coerce")

        if direction == "LONG":
            mfe_r = (highs.max() - entry) / risk
            close_r = (closes.iloc[-1] - entry) / risk
        else:
            mfe_r = (entry - lows.min()) / risk
            close_r = (entry - closes.iloc[-1]) / risk

        hours = n * 0.25
        tag = str(hours).rstrip("0").rstrip(".").replace(".", "_")
        out[f"V155_MFE_{tag}h_R"] = float(mfe_r) if math.isfinite(mfe_r) else math.nan
        out[f"V155_close_{tag}h_R"] = float(close_r) if math.isfinite(close_r) else math.nan

    # 候選旗標：目前只診斷，不提前退場。
    mfe_1h = number(out.get("V155_MFE_1h_R"))
    close_1h = number(out.get("V155_close_1h_R"))
    mfe_2h = number(out.get("V155_MFE_2h_R"))
    close_2h = number(out.get("V155_close_2h_R"))
    mfe_3h = number(out.get("V155_MFE_3h_R"))
    close_3h = number(out.get("V155_close_3h_R"))

    out["V155_no_progress_1h_flag"] = bool(
        math.isfinite(mfe_1h) and math.isfinite(close_1h)
        and mfe_1h < 0.25 and close_1h < 0
    )
    out["V155_no_progress_2h_flag"] = bool(
        math.isfinite(mfe_2h) and math.isfinite(close_2h)
        and mfe_2h < 0.50 and close_2h < 0
    )
    out["V155_no_progress_3h_flag"] = bool(
        math.isfinite(mfe_3h) and math.isfinite(close_3h)
        and mfe_3h < 0.75 and close_3h < 0
    )
    return out

def rr_with_prices(direction: str, entry: float, stop: float, tp1: float) -> float:
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, tp1)):
        return math.nan
    risk = abs(entry - stop)
    if risk <= 0:
        return math.nan
    if direction == "LONG":
        return (tp1 - entry) / risk
    if direction == "SHORT":
        return (entry - tp1) / risk
    return math.nan


def max_entry_for_rr_long(tp1: float, stop: float, min_rr: float = MIN_RR) -> float:
    # (TP-entry)/(entry-stop) >= R
    # => entry <= (TP + R*stop)/(R+1)
    if not all(math.isfinite(x) and x > 0 for x in (tp1, stop, min_rr)):
        return math.nan
    return (tp1 + min_rr * stop) / (min_rr + 1.0)


def max_entry_for_stop_pct_long(stop: float, max_stop_pct: float) -> float:
    # (entry-stop)/entry <= p  => entry <= stop/(1-p)
    if not math.isfinite(stop) or stop <= 0 or not (0 < max_stop_pct < 1):
        return math.nan
    return stop / (1.0 - max_stop_pct)


def find_reprice_fill_long(
    frame: pd.DataFrame,
    signal_time: pd.Timestamp,
    limit_entry: float,
    structural_stop: float,
    tp1: float,
) -> tuple[pd.Timestamp | None, str]:
    """
    訊號後掛回踩限價單，最多等 12 根 15m（3小時）。
    保守處理：
    - 同根若已破 structural_stop：取消。
    - 同根若已碰原 TP1：行情跑掉，不追。
    - 只有 low <= limit 且沒碰 stop/TP1，才算成交。
    """
    if frame.empty or pd.isna(signal_time):
        return None, "無K線"

    bars = frame[frame.index >= signal_time].head(REPRICE_WAIT_BARS + 1)
    if bars.empty:
        return None, "無等待K線"

    for ts, b in bars.iterrows():
        lo = number(b.get("low"))
        hi = number(b.get("high"))
        if not math.isfinite(lo) or not math.isfinite(hi):
            continue

        if lo <= structural_stop:
            return None, "等待回踩前結構已破壞"

        if hi >= tp1:
            return None, "等待回踩前已碰原TP1_行情跑掉"

        if lo <= limit_entry:
            return pd.Timestamp(ts), "回踩限價成交"

    return None, f"{REPRICE_WAIT_BARS}根內未回踩"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法載入：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def verify_inputs() -> None:
    if not PARITY_PATH.exists():
        raise FileNotFoundError(f"找不到 V006.1 parity：{PARITY_PATH}")

    missing = []
    for year, cfg in PERIOD_CONFIG.items():
        for p in list(cfg["candidate"]) + [cfg["quality"]]:
            if not p.exists():
                missing.append(f"{year}: {p}")
    if missing:
        raise FileNotFoundError(
            "缺少已完成的 ORIGINAL 上游結果：\n" + "\n".join(missing)
        )

    if not CACHE_2023.exists():
        raise FileNotFoundError(f"2023 cache不存在：{CACHE_2023}")


# ---------------------------------------------------------------------
# 2023 已重建歷史 K 線
# ---------------------------------------------------------------------

def cache_path_2023(exchange: str, symbol: str) -> Path | None:
    ids = [f"{exchange}|{symbol}|15m|365"]
    if exchange != "Binance":
        ids.insert(0, ids[0] + "|gapfix_v1")

    for ident in ids:
        key = hashlib.sha256(ident.encode()).hexdigest()
        for p in (
            CACHE_2023 / f"stable_{key}.pkl.gz",
            CACHE_2023 / f"{key}.pkl.gz",
        ):
            if p.exists():
                return p
    return None


def load_frame_2023(exchange: str, symbol: str) -> pd.DataFrame:
    p = cache_path_2023(exchange, symbol)
    if p is None:
        return pd.DataFrame()

    try:
        with gzip.open(p, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        return pd.DataFrame()

    d = payload[0] if isinstance(payload, tuple) else payload
    if not isinstance(d, pd.DataFrame) or d.empty:
        return pd.DataFrame()

    return normalize_frame(d)


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()

    d = frame.copy()
    d.index = pd.to_datetime(d.index, utc=True, errors="coerce")
    d = d[~d.index.isna()]
    d = d[~d.index.duplicated(keep="last")].sort_index()

    for c in ("open", "high", "low", "close", "volume"):
        if c not in d.columns:
            return pd.DataFrame()
        d[c] = pd.to_numeric(d[c], errors="coerce")

    return d.dropna(subset=["open", "high", "low", "close", "volume"])


# ---------------------------------------------------------------------
# 因果結構停損
# ---------------------------------------------------------------------

def atr_before_entry(frame: pd.DataFrame, entry_time: pd.Timestamp) -> float:
    hist = frame[frame.index < entry_time]
    if len(hist) < 15:
        return math.nan

    h = pd.to_numeric(hist["high"], errors="coerce")
    l = pd.to_numeric(hist["low"], errors="coerce")
    c = pd.to_numeric(hist["close"], errors="coerce")
    prev = c.shift(1)

    tr = pd.concat(
        [(h - l).abs(), (h - prev).abs(), (l - prev).abs()],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(14, min_periods=14).mean().iloc[-1]
    return number(atr)


def recent_confirmed_pivot(
    frame: pd.DataFrame,
    entry_time: pd.Timestamp,
    direction: str,
) -> tuple[float, pd.Timestamp | None]:
    """
    只看 entry_time 以前已完成 K 線。
    LONG 找最近 confirmed swing low；
    SHORT 找最近 confirmed swing high。
    pivot 左右各 2 根都必須已完成。
    """
    hist = frame[frame.index < entry_time].copy()
    need = STRUCT_LOOKBACK_BARS + PIVOT_LEFT + PIVOT_RIGHT
    if len(hist) < max(15, need):
        return math.nan, None

    # 多留左右確認所需的 bars，但 pivot 本身限制在最近 LOOKBACK 範圍。
    w = hist.tail(STRUCT_LOOKBACK_BARS + PIVOT_LEFT + PIVOT_RIGHT + 4)
    lows = pd.to_numeric(w["low"], errors="coerce").to_numpy()
    highs = pd.to_numeric(w["high"], errors="coerce").to_numpy()
    idx = w.index

    # 從最近往前找，避免拿很久以前的極端低點/高點。
    earliest = max(PIVOT_LEFT, len(w) - STRUCT_LOOKBACK_BARS - PIVOT_RIGHT)
    latest = len(w) - PIVOT_RIGHT - 1

    for i in range(latest, earliest - 1, -1):
        if direction == "LONG":
            x = lows[i]
            if not math.isfinite(float(x)):
                continue
            left = lows[i - PIVOT_LEFT:i]
            right = lows[i + 1:i + 1 + PIVOT_RIGHT]
            if (
                all(math.isfinite(float(v)) for v in left)
                and all(math.isfinite(float(v)) for v in right)
                and x <= min(left)
                and x <= min(right)
                and (x < min(left) or x < min(right))
            ):
                return float(x), idx[i]

        elif direction == "SHORT":
            x = highs[i]
            if not math.isfinite(float(x)):
                continue
            left = highs[i - PIVOT_LEFT:i]
            right = highs[i + 1:i + 1 + PIVOT_RIGHT]
            if (
                all(math.isfinite(float(v)) for v in left)
                and all(math.isfinite(float(v)) for v in right)
                and x >= max(left)
                and x >= max(right)
                and (x > max(left) or x > max(right))
            ):
                return float(x), idx[i]

    return math.nan, None


def structure_stop(
    row: dict,
    frame: pd.DataFrame,
) -> dict:
    """
    回傳結構停損提案。
    只放寬，不縮緊。
    """
    direction = side(row)
    entry = number(row.get("entry"))
    old_stop = number(row.get("stop"))
    when = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")

    result = {
        "structure_ok": False,
        "old_stop": old_stop,
        "pivot": math.nan,
        "pivot_time": None,
        "atr": math.nan,
        "raw_structure_stop": math.nan,
        "final_stop": old_stop,
        "widened": False,
        "reason": "",
    }

    if direction not in {"LONG", "SHORT"}:
        result["reason"] = "方向缺失"
        return result
    if pd.isna(when):
        result["reason"] = "entry_time無效"
        return result
    if not all(math.isfinite(x) and x > 0 for x in (entry, old_stop)):
        result["reason"] = "Entry/Stop無效"
        return result
    if frame.empty:
        result["reason"] = "歷史K線不存在"
        return result

    atr = atr_before_entry(frame, when)
    pivot, pivot_time = recent_confirmed_pivot(frame, when, direction)

    result["atr"] = atr
    result["pivot"] = pivot
    result["pivot_time"] = pivot_time

    if not math.isfinite(atr) or atr <= 0:
        result["reason"] = "進場前ATR不足"
        return result
    if not math.isfinite(pivot):
        result["reason"] = "24根內找不到已確認前低/前高"
        return result

    if direction == "LONG":
        raw = pivot - STRUCTURE_BUFFER_ATR * atr
        if raw >= entry or raw <= 0:
            result["reason"] = "LONG結構停損位置無效"
            return result
        # 原stop若已經更低，代表本來就已在結構外，不縮緊。
        final_stop = min(old_stop, raw)
        widened = final_stop < old_stop - max(entry * 1e-12, 1e-15)
    else:
        raw = pivot + STRUCTURE_BUFFER_ATR * atr
        if raw <= entry:
            result["reason"] = "SHORT結構停損位置無效"
            return result
        final_stop = max(old_stop, raw)
        widened = final_stop > old_stop + max(entry * 1e-12, 1e-15)

    result.update({
        "structure_ok": True,
        "raw_structure_stop": raw,
        "final_stop": final_stop,
        "widened": widened,
        "reason": "PASS",
    })
    return result


# ---------------------------------------------------------------------
# A/B 候選規則
# ---------------------------------------------------------------------

def base_gate(row: dict) -> tuple[bool, str]:
    direction = side(row)
    module = module_name(row).upper()
    rr = price_rr(row)

    if direction not in {"LONG", "SHORT"}:
        return False, "方向缺失"

    if not math.isfinite(rr):
        return False, "RR無法由價格重算"

    if rr < MIN_RR - 1e-6:
        return False, f"全策略最低4R：原始{rr:.3f}R"

    if "V72.8S" in module and direction == "LONG":
        return False, "關閉V72.8S LONG"

    if "V85" in module:
        if direction != "LONG":
            return False, "V85僅允許LONG"
        slope = number(row.get("ema20_slope8_atr"))
        if not math.isfinite(slope):
            return False, "V85缺少ema20_slope8_atr"
        if slope > V85_MAX_EMA20_SLOPE_ATR + 1e-12:
            return False, f"V85趨勢過度延伸：{slope:.3f} > 0.60"

    return True, "PASS"


def transform_v0063(row: dict) -> tuple[dict | None, dict]:
    """完全對照目前 V150 ELITE 候選門。"""
    ok, reason = base_gate(row)
    audit = {
        "variant": "V150_ELITE",
        "keep": False,
        "reason": reason,
        "old_stop": number(row.get("stop")),
        "final_stop": number(row.get("stop")),
        "old_stop_pct": stop_pct(row),
        "final_stop_pct": stop_pct(row),
        "rr_before": price_rr(row),
        "rr_after": price_rr(row),
        "structure_changed": False,
    }

    if not ok:
        return None, audit

    module = module_name(row).upper()
    if "V85" in module:
        sp = stop_pct(row)
        if not math.isfinite(sp):
            audit["reason"] = "V85缺少stop_pct"
            return None, audit
        if sp > V85_ORIGINAL_MAX_STOP_PCT + 1e-12:
            audit["reason"] = f"V85停損過寬：{sp*100:.2f}% > 2.00%"
            return None, audit

    audit["keep"] = True
    audit["reason"] = "PASS"
    return copy.deepcopy(row), audit


def transform_v154(row: dict, frame: pd.DataFrame) -> tuple[dict | None, dict]:
    """
    V154 結構進場：
    - 原 TP1/TP2/TP3 不往外推。
    - 先找已確認前低，SL 放在前低外 0.15 ATR。
    - 現在 Entry 合格：直接用合理結構 SL。
    - 現在 Entry 太高：反算最高可接受 Entry，3小時內回踩才成交。
    - 合理 SL 太遠 / 仍不足4R / 回踩前破結構：不進。
    """
    ok, reason = base_gate(row)
    module = module_name(row).upper()
    direction = side(row)

    old_entry = number(row.get("entry"))
    old_stop = number(row.get("stop"))
    tp1 = number(row.get("tp1"))
    signal_time = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")

    audit = {
        "variant": "V154_STRUCTURE_ENTRY",
        "keep": False,
        "reason": reason,
        "old_entry": old_entry,
        "final_entry": old_entry,
        "old_stop": old_stop,
        "final_stop": old_stop,
        "old_stop_pct": stop_pct(row),
        "final_stop_pct": stop_pct(row),
        "rr_before": price_rr(row),
        "rr_after": price_rr(row),
        "pivot": math.nan,
        "pivot_time": None,
        "atr": math.nan,
        "structural_stop": math.nan,
        "risk_growth_pct": 0.0,
        "max_entry_by_rr": math.nan,
        "max_entry_by_risk_growth": math.nan,
        "max_entry_by_stop_pct": math.nan,
        "max_acceptable_entry": math.nan,
        "repriced": False,
        "reprice_fill_time": None,
    }

    if not ok:
        return None, audit

    # 保留 V150 的 V85 原始 stop <=2% 門檻。
    if "V85" in module:
        original_sp = stop_pct(row)
        if not math.isfinite(original_sp):
            audit["reason"] = "V85缺少stop_pct"
            return None, audit
        if original_sp > V85_ORIGINAL_MAX_STOP_PCT + 1e-12:
            audit["reason"] = f"V85原停損過寬：{original_sp*100:.2f}% > 2.00%"
            return None, audit

    is_target = (
        direction == "LONG"
        and any(token in module for token in TARGET_STRUCTURE_MODULES)
    )

    # 先只動 V85/V96 LONG；V72 SHORT / V88 等維持原策略。
    if not is_target:
        audit["keep"] = True
        audit["reason"] = "PASS_非V85V96_LONG_原策略"
        return copy.deepcopy(row), audit

    if frame.empty or pd.isna(signal_time):
        audit["keep"] = True
        audit["reason"] = "PASS_資料不足_維持原策略"
        return copy.deepcopy(row), audit

    atr = atr_before_entry(frame, signal_time)
    pivot, pivot_time = recent_confirmed_pivot(frame, signal_time, direction)
    audit["atr"] = atr
    audit["pivot"] = pivot
    audit["pivot_time"] = pivot_time

    if not math.isfinite(atr) or atr <= 0 or not math.isfinite(pivot):
        audit["keep"] = True
        audit["reason"] = "PASS_無已確認結構_維持原策略"
        return copy.deepcopy(row), audit

    if not all(math.isfinite(x) and x > 0 for x in (old_entry, old_stop, tp1)):
        audit["reason"] = "Entry/Stop/TP1無效"
        return None, audit

    structural_stop = pivot - STRUCTURE_BUFFER_ATR * atr
    final_stop = min(old_stop, structural_stop)

    audit["structural_stop"] = structural_stop
    audit["final_stop"] = final_stop

    if not math.isfinite(final_stop) or final_stop <= 0 or final_stop >= old_entry:
        audit["reason"] = "結構停損位置無效"
        return None, audit

    # 原停損本來就更安全，不縮緊。
    if final_stop >= old_stop - max(old_entry * 1e-12, 1e-15):
        audit["keep"] = True
        audit["reason"] = "PASS_原SL已在結構外_不動"
        return copy.deepcopy(row), audit

    old_risk = old_entry - old_stop
    if old_risk <= 0:
        audit["reason"] = "原風險距離無效"
        return None, audit

    max_stop_pct = (
        V85_FINAL_MAX_STOP_PCT if "V85" in module else V96_FINAL_MAX_STOP_PCT
    )

    # 三個條件一起反算「最多可以追到哪個 Entry」。
    e_rr = max_entry_for_rr_long(tp1, final_stop, MIN_RR)
    e_growth = final_stop + (1.0 + MAX_RISK_GROWTH) * old_risk
    e_stop = max_entry_for_stop_pct_long(final_stop, max_stop_pct)
    max_entry = min(e_rr, e_growth, e_stop)

    audit.update({
        "max_entry_by_rr": e_rr,
        "max_entry_by_risk_growth": e_growth,
        "max_entry_by_stop_pct": e_stop,
        "max_acceptable_entry": max_entry,
    })

    if not math.isfinite(max_entry) or max_entry <= final_stop:
        audit["reason"] = "反算可接受Entry失敗"
        return None, audit

    # A. 現在 Entry 已經合格：直接用合理結構 SL，原 TP 不動。
    if old_entry <= max_entry + max(old_entry * 1e-12, 1e-15):
        new_risk = old_entry - final_stop
        growth = new_risk / old_risk - 1.0
        sp = new_risk / old_entry
        rr = rr_with_prices(direction, old_entry, final_stop, tp1)

        if growth > MAX_RISK_GROWTH + 1e-8 or sp > max_stop_pct + 1e-8 or rr < MIN_RR - 1e-6:
            audit["reason"] = "直接進驗算失敗"
            return None, audit

        new_row = copy.deepcopy(row)
        new_row["stop"] = final_stop
        new_row["stop_pct"] = sp
        new_row["risk_price"] = new_risk
        new_row["rr"] = rr
        new_row["rr1"] = rr

        new_row["V154原Entry"] = old_entry
        new_row["V154最終Entry"] = old_entry
        new_row["V154原停損"] = old_stop
        new_row["V154結構點"] = pivot
        new_row["V154結構點時間"] = str(pivot_time)
        new_row["V154進場前ATR"] = atr
        new_row["V154結構緩衝ATR"] = STRUCTURE_BUFFER_ATR
        new_row["V154結構停損"] = final_stop
        new_row["V154風險增加_pct"] = growth * 100.0
        new_row["V154原TP1不變"] = tp1
        new_row["V154結構後RR"] = rr
        new_row["V154模式"] = "直接進_只修SL"

        audit.update({
            "keep": True,
            "reason": f"PASS_直接結構SL｜risk+{growth*100:.1f}%｜RR={rr:.2f}",
            "final_stop_pct": sp,
            "rr_after": rr,
            "risk_growth_pct": growth * 100.0,
        })
        return new_row, audit

    # B. 現在 Entry 太高：不推 TP，改等回踩到最高可接受 Entry。
    fill_time, fill_reason = find_reprice_fill_long(
        frame=frame,
        signal_time=signal_time,
        limit_entry=max_entry,
        structural_stop=final_stop,
        tp1=tp1,
    )

    if fill_time is None:
        audit["reason"] = f"不追高_最高可接受Entry={max_entry:.8g}｜{fill_reason}"
        return None, audit

    new_entry = max_entry
    new_risk = new_entry - final_stop
    new_stop_pct = new_risk / new_entry
    new_rr = rr_with_prices(direction, new_entry, final_stop, tp1)
    growth = new_risk / old_risk - 1.0

    if growth > MAX_RISK_GROWTH + 1e-8:
        audit["reason"] = "回踩Entry後風險增幅仍超30%"
        return None, audit
    if new_stop_pct > max_stop_pct + 1e-8:
        audit["reason"] = "回踩Entry後stop_pct仍過寬"
        return None, audit
    if new_rr < MIN_RR - 1e-6:
        audit["reason"] = f"回踩Entry後RR={new_rr:.3f}<4R"
        return None, audit

    new_row = copy.deepcopy(row)
    new_row["entry_time"] = fill_time
    new_row["entry"] = new_entry
    new_row["stop"] = final_stop
    new_row["stop_pct"] = new_stop_pct
    new_row["risk_price"] = new_risk
    new_row["rr"] = new_rr
    new_row["rr1"] = new_rr

    # TP1/TP2/TP3 保留原值。
    new_row["V154原Entry"] = old_entry
    new_row["V154最終Entry"] = new_entry
    new_row["V154原訊號時間"] = str(signal_time)
    new_row["V154回踩成交時間"] = str(fill_time)
    new_row["V154原停損"] = old_stop
    new_row["V154結構點"] = pivot
    new_row["V154結構點時間"] = str(pivot_time)
    new_row["V154進場前ATR"] = atr
    new_row["V154結構緩衝ATR"] = STRUCTURE_BUFFER_ATR
    new_row["V154結構停損"] = final_stop
    new_row["V154風險增加_pct"] = growth * 100.0
    new_row["V154原TP1不變"] = tp1
    new_row["V154結構後RR"] = new_rr
    new_row["V154最高可接受Entry"] = max_entry
    new_row["V154模式"] = "等回踩重新定價"

    audit.update({
        "keep": True,
        "reason": f"PASS_回踩重新定價｜{old_entry:.8g}->{new_entry:.8g}｜RR={new_rr:.2f}",
        "final_entry": new_entry,
        "final_stop": final_stop,
        "final_stop_pct": new_stop_pct,
        "rr_after": new_rr,
        "risk_growth_pct": growth * 100.0,
        "repriced": True,
        "reprice_fill_time": fill_time,
    })
    return new_row, audit


# ---------------------------------------------------------------------
# 三年正式重排倉
# ---------------------------------------------------------------------

def reason_to_chinese(value: Any) -> str:
    s = str(value or "")
    replacements = [
        ("PASS_", "通過_"),
        ("PASS", "通過"),
        ("LONG", "做多"),
        ("SHORT", "做空"),
        ("Entry", "進場"),
        ("entry", "進場"),
        ("Stop", "停損"),
        ("stop", "停損"),
        ("SL", "停損"),
        ("TP", "停利"),
        ("RR", "風報比"),
        ("risk", "風險"),
        ("reprice", "重新定價"),
        ("structure", "結構"),
        ("pivot", "轉折點"),
    ]
    for a, b in replacements:
        s = s.replace(a, b)
    return s


def direction_to_chinese(value: str) -> str:
    return "做多" if value == "LONG" else "做空" if value == "SHORT" else str(value)


def build_user_audit_row(
    year: str,
    source_row: dict,
    audit: dict,
) -> dict:
    return {
        "年度": year,
        "交易所": source_row.get("exchange"),
        "標的": source_row.get("symbol"),
        "原進場時間": source_row.get("entry_time"),
        "方向": direction_to_chinese(side(source_row)),
        "模組": module_name(source_row),
        "最後是否保留": "是" if audit.get("keep") else "否",
        "最後原因": reason_to_chinese(audit.get("reason")),
        "無動能規則結果": reason_to_chinese(audit.get("V155_failure_guard")),
        "進場有效性結果": reason_to_chinese(audit.get("V155_entry_validity")),
        "原進場價": audit.get("old_entry"),
        "最終進場價": audit.get("final_entry"),
        "原停損價": audit.get("old_stop"),
        "最終停損價": audit.get("final_stop"),
        "原停損距離_pct": (
            number(audit.get("old_stop_pct")) * 100
            if math.isfinite(number(audit.get("old_stop_pct")))
            else math.nan
        ),
        "最終停損距離_pct": (
            number(audit.get("final_stop_pct")) * 100
            if math.isfinite(number(audit.get("final_stop_pct")))
            else math.nan
        ),
        "原風報比": audit.get("rr_before"),
        "最終風報比": audit.get("rr_after"),
        "重新計算成功": "是" if audit.get("V155_recalc_ok") else "否",
        "重新計算缺少原因": audit.get("V155_missing_reason"),
        "訊號時間": audit.get("V155_signal_time"),
        "訊號ATR": audit.get("V155_atr"),
        "成交量比": audit.get("V155_volume_ratio"),
        "量能堆積": audit.get("V155_volume_build"),
        "20期均線8根斜率_ATR": audit.get("V155_ema20_slope8_atr"),
        "進場前8根漲幅_pct": (
            number(audit.get("V155_ret8")) * 100
            if math.isfinite(number(audit.get("V155_ret8")))
            else math.nan
        ),
        "前20根區間_ATR": audit.get("V155_range_atr"),
        "上方空間_R": audit.get("V155_room_r"),
        "重新計算候選分數": audit.get("V155_score"),
        "結構距離_ATR": audit.get("V155_structure_distance_atr"),
        "訊號K收盤位置": audit.get("V155_signal_close_location"),
        "弱點數": audit.get("V155_bad_count"),
        "弱點": audit.get("V155_bad_flags"),
        "進場K開盤價": audit.get("V155_entry_open"),
        "進場當下停損已失效": "是" if audit.get("V155_entry_stop_invalid") else "否",
        "1小時最大順向_R": audit.get("V155_MFE_1h_R"),
        "1小時收盤_R": audit.get("V155_close_1h_R"),
        "2小時最大順向_R": audit.get("V155_MFE_2h_R"),
        "2小時收盤_R": audit.get("V155_close_2h_R"),
        "3小時最大順向_R": audit.get("V155_MFE_3h_R"),
        "3小時收盤_R": audit.get("V155_close_3h_R"),
        "1小時無進展標記": "是" if audit.get("V155_no_progress_1h_flag") else "否",
        "2小時無進展標記": "是" if audit.get("V155_no_progress_2h_flag") else "否",
        "3小時無進展標記": "是" if audit.get("V155_no_progress_3h_flag") else "否",
    }


def run_variant(variant: str) -> tuple[list[dict], dict[str, dict]]:
    parity = load_module(f"v155_recalc_parity_{variant}", PARITY_PATH)

    internal_out = OUT / "內部引擎結果"
    internal_out.mkdir(parents=True, exist_ok=True)
    parity.HERE = internal_out

    for year, cfg in PERIOD_CONFIG.items():
        parity.PERIODS[year]["candidate"] = cfg["candidate"]
        parity.PERIODS[year]["quality"] = cfg["quality"]
        parity.PERIODS[year]["source_note"] = f"{cfg['source_note']}｜V155重新計算版"

    original_load_candidates = parity.load_candidates
    original_load_base = parity.load_base

    current = {"year": None}
    audits: dict[str, list[dict]] = {}
    user_audits: dict[str, list[dict]] = {}
    gate_stats: dict[str, dict] = {}
    base_modules: dict[str, Any] = {}

    def base_for_year(year: str):
        if year in base_modules:
            return base_modules[year]
        if year == "2023":
            obj = SimpleNamespace(load_frame=load_frame_2023)
        else:
            obj = original_load_base(year)
        base_modules[year] = obj
        return obj

    def frame_for(year: str, exchange: str, symbol: str) -> pd.DataFrame:
        key = (year, str(exchange), str(symbol))
        if key in FRAME_CACHE:
            return FRAME_CACHE[key]

        base = base_for_year(year)
        try:
            d = base.load_frame(str(exchange), str(symbol))
        except Exception:
            d = pd.DataFrame()

        d = normalize_frame(d)
        FRAME_CACHE[key] = d
        return d

    def candidate_loader(source):
        rows = original_load_candidates(source)
        year = str(current["year"])
        kept = []
        audit_rows = []
        user_rows = []

        stat = {
            "年度": year,
            "原始候選數": len(rows),
            "V154通過候選數": 0,
            "V154原規則篩掉數": 0,
            "V155目標檢查數": 0,
            "K線重新計算成功數": 0,
            "資料不足放行數": 0,
            "V85無動能擋下數": 0,
            "V96無動能擋下數": 0,
            "進場當下停損失效擋下數": 0,
            "V155新規則總擋下數": 0,
            "最後保留候選數": 0,
        }

        for row in rows:
            ex = str(row.get("exchange") or "")
            sym = str(row.get("symbol") or "")
            module = module_name(row).upper()
            direction = side(row)

            needs_frame = (
                direction == "LONG"
                and any(token in module for token in TARGET_STRUCTURE_MODULES)
            )

            if needs_frame:
                frame = frame_for(year, ex, sym)
            else:
                frame = pd.DataFrame()

            transformed, audit = transform_v154(row, frame)

            if transformed is None:
                stat["V154原規則篩掉數"] += 1
            else:
                stat["V154通過候選數"] += 1

                if needs_frame:
                    stat["V155目標檢查數"] += 1

                    fg_ok, fg_reason, fg_detail = v155_failure_guard(row, frame)
                    audit.update(fg_detail)
                    audit["V155_failure_guard"] = fg_reason

                    if fg_detail.get("V155_recalc_ok"):
                        stat["K線重新計算成功數"] += 1
                    else:
                        stat["資料不足放行數"] += 1

                    if not fg_ok:
                        audit["keep"] = False
                        audit["reason"] = fg_reason
                        transformed = None
                        stat["V155新規則總擋下數"] += 1

                        if "V85" in module:
                            stat["V85無動能擋下數"] += 1
                        elif "V96" in module:
                            stat["V96無動能擋下數"] += 1

                else:
                    audit["V155_failure_guard"] = "非V85/V96做多，不加無動能門"

            # HIVE 類型：只有前面規則仍打算進場時才檢查。
            if transformed is not None and not frame.empty:
                ev_ok, ev_reason, ev_detail = v155_entry_validity_guard(
                    transformed, frame
                )
                audit.update(ev_detail)
                audit["V155_entry_validity"] = ev_reason

                if not ev_ok:
                    audit["keep"] = False
                    audit["reason"] = ev_reason
                    transformed = None
                    stat["進場當下停損失效擋下數"] += 1
                    stat["V155新規則總擋下數"] += 1

            elif "V155_entry_validity" not in audit:
                audit["V155_entry_validity"] = "未檢查或不需檢查"

            # 1/2/3小時仍只做診斷，不修改本版績效。
            if transformed is not None and not frame.empty:
                diag = v155_no_progress_diagnostic(transformed, frame)
                audit.update(diag)

            full_audit = {
                "exchange": row.get("exchange"),
                "symbol": row.get("symbol"),
                "entry_time": row.get("entry_time"),
                "direction": side(row),
                "module": module_name(row),
                **audit,
            }
            audit_rows.append(full_audit)
            user_rows.append(build_user_audit_row(year, row, full_audit))

            if transformed is not None:
                kept.append(transformed)

        stat["最後保留候選數"] = len(kept)
        audits[year] = audit_rows
        user_audits[year] = user_rows
        gate_stats[year] = stat

        print(
            f"{year} 候選驗收：原始{stat['原始候選數']}筆，"
            f"V154通過{stat['V154通過候選數']}筆，"
            f"V155重新計算成功{stat['K線重新計算成功數']}筆，"
            f"資料不足放行{stat['資料不足放行數']}筆，"
            f"V85擋下{stat['V85無動能擋下數']}筆，"
            f"V96擋下{stat['V96無動能擋下數']}筆，"
            f"進場失效擋下{stat['進場當下停損失效擋下數']}筆，"
            f"最後保留{stat['最後保留候選數']}筆"
        )
        return kept

    def base_loader(period: str):
        return base_for_year(period)

    parity.load_candidates = candidate_loader
    parity.load_base = base_loader

    summaries = []

    for year in ("2023", "2024", "2025"):
        print("\n" + "=" * 108)
        print(f"V155 敗單防護｜{year}")
        print("基礎：V154 結構停損＋進場重新定價")
        print("新增：進場前K線重新計算無動能條件＋進場當下停損有效性")
        print("1、2、3小時無進展：只記錄，不提前平倉")
        print("=" * 108)

        current["year"] = year
        summary = parity.run(year)

        summary["測試版本"] = "V155 敗單防護"
        summary["V154基準淨利_USDT"] = KNOWN_V154[year]["pnl"]
        summary["相對V154改善_USDT"] = round(
            float(summary.get("已實現淨利_USDT", 0.0))
            - KNOWN_V154[year]["pnl"],
            4,
        )
        summary["V150基準淨利_USDT"] = KNOWN_V150[year]["pnl"]
        summary["相對V150改善_USDT"] = round(
            float(summary.get("已實現淨利_USDT", 0.0))
            - KNOWN_V150[year]["pnl"],
            4,
        )
        summary["ORIGINAL基準淨利_USDT"] = KNOWN_ORIGINAL[year]["pnl"]
        summary["相對ORIGINAL改善_USDT"] = round(
            float(summary.get("已實現淨利_USDT", 0.0))
            - KNOWN_ORIGINAL[year]["pnl"],
            4,
        )
        summaries.append(summary)

        pd.DataFrame(user_audits.get(year, [])).to_csv(
            OUT / f"{year}_逐筆候選與規則驗收.csv",
            index=False,
            encoding="utf-8-sig",
        )

    # --------------------------------------------------------
    # 強制驗收：避免再出現「改很多但一筆都沒真的改到」。
    # --------------------------------------------------------
    stats_df = pd.DataFrame([gate_stats[y] for y in ("2023", "2024", "2025")])
    stats_df.to_csv(
        OUT / "V155_規則生效統計.csv",
        index=False,
        encoding="utf-8-sig",
    )

    total_target = int(stats_df["V155目標檢查數"].sum())
    total_recalc = int(stats_df["K線重新計算成功數"].sum())
    total_blocked = int(stats_df["V155新規則總擋下數"].sum())

    print("\n" + "=" * 108)
    print("V155 新規則生效驗收")
    print(stats_df.to_string(index=False))
    print("=" * 108)

    if total_target <= 0:
        raise RuntimeError("V155驗收失敗：三年沒有任何V85/V96做多候選被檢查。")

    if total_recalc <= 0:
        raise RuntimeError("V155驗收失敗：三年K線特徵重新計算成功數為0。")

    if total_blocked <= 0:
        raise RuntimeError(
            "V155驗收失敗：新規則三年一筆都沒有實際擋下，"
            "拒絕把它當成有效新版本。"
        )

    return summaries, gate_stats


def robust_score(rows: list[dict]) -> dict:
    """
    不拿單年最高獲利直接選王。
    只提供幾個跨年指標，最後仍人工看三年。
    """
    pnls = [float(x.get("已實現淨利_USDT", 0.0)) for x in rows]
    dds = [float(x.get("最大回撤_pct", math.nan)) for x in rows]
    errors = [int(x.get("錯誤", 0) or 0) for x in rows]

    finite_dd = [x for x in dds if math.isfinite(x)]
    return {
        "三年淨利_USDT": round(sum(pnls), 4),
        "最差年度淨利_USDT": round(min(pnls), 4),
        "正報酬年度數": int(sum(x > 0 for x in pnls)),
        "最大年度DD_pct": round(max(finite_dd), 4) if finite_dd else math.nan,
        "錯誤總數": int(sum(errors)),
    }


def main() -> int:
    verify_inputs()
    OUT.mkdir(parents=True, exist_ok=True)

    print("=" * 108)
    print("V155 敗單防護｜三年重新回測")
    print("基礎：V154 結構停損＋進場重新定價")
    print("這次無動能條件全部由進場前15分鐘K線重新計算，不再依賴候選表缺失欄位。")
    print("若三年新規則一筆都沒有真正擋下，程式會直接判定失敗。")
    print("=" * 108)

    summaries, gate_stats = run_variant("V155_RECALC")

    comp = pd.DataFrame(summaries)

    # 只輸出使用者真正要看的中文欄位。
    result_rows = []
    for _, r in comp.iterrows():
        result_rows.append({
            "期間": r.get("期間"),
            "交易數": r.get("交易數"),
            "TP1筆數": r.get("TP1筆數"),
            "TP1命中率_pct": r.get("總勝率_pct"),
            "已實現淨利_U": r.get("已實現淨利_USDT"),
            "最大回撤_pct": r.get("最大回撤_pct"),
            "獲利因子": r.get("Profit_Factor"),
            "做多交易": r.get("做多交易"),
            "做空交易": r.get("做空交易"),
            "相對V154改善_U": r.get("相對V154改善_USDT"),
            "相對V150改善_U": r.get("相對V150改善_USDT"),
            "相對原始版改善_U": r.get("相對ORIGINAL改善_USDT"),
            "錯誤": r.get("錯誤"),
        })

    result_df = pd.DataFrame(result_rows)
    result_df.to_csv(
        OUT / "V155_三年結果.csv",
        index=False,
        encoding="utf-8-sig",
    )

    score = robust_score(summaries)

    baseline_df = pd.DataFrame([
        {
            "版本": "原始版",
            "三年淨利_U": round(sum(x["pnl"] for x in KNOWN_ORIGINAL.values()), 4),
            "最差年度淨利_U": min(x["pnl"] for x in KNOWN_ORIGINAL.values()),
            "正報酬年度數": sum(x["pnl"] > 0 for x in KNOWN_ORIGINAL.values()),
        },
        {
            "版本": "V150 精選版",
            "三年淨利_U": round(sum(x["pnl"] for x in KNOWN_V150.values()), 4),
            "最差年度淨利_U": min(x["pnl"] for x in KNOWN_V150.values()),
            "正報酬年度數": sum(x["pnl"] > 0 for x in KNOWN_V150.values()),
        },
        {
            "版本": "V154 結構進場版",
            "三年淨利_U": round(sum(x["pnl"] for x in KNOWN_V154.values()), 4),
            "最差年度淨利_U": min(x["pnl"] for x in KNOWN_V154.values()),
            "正報酬年度數": sum(x["pnl"] > 0 for x in KNOWN_V154.values()),
        },
        {
            "版本": "V155 敗單防護版",
            "三年淨利_U": score["三年淨利_USDT"],
            "最差年度淨利_U": score["最差年度淨利_USDT"],
            "正報酬年度數": score["正報酬年度數"],
            "最大年度回撤_pct": score["最大年度DD_pct"],
            "錯誤總數": score["錯誤總數"],
        },
    ])

    baseline_df.to_csv(
        OUT / "V155_跨年比較.csv",
        index=False,
        encoding="utf-8-sig",
    )

    stats_df = pd.DataFrame([gate_stats[y] for y in ("2023", "2024", "2025")])
    total_blocked = int(stats_df["V155新規則總擋下數"].sum())
    total_recalc = int(stats_df["K線重新計算成功數"].sum())
    total_missing = int(stats_df["資料不足放行數"].sum())

    final_report = {
        "版本": "V155 敗單防護版",
        "狀態": "三年研究回測完成",
        "基礎": "V154 結構停損與進場重新定價",
        "這次真正修改": [
            "無動能條件改為直接從進場前15分鐘K線重新計算",
            "重新計算成交量比、量能堆積、20期均線斜率、進場前8根漲幅、前20根區間、上方空間",
            "V85候選分數在可辨識原始進場型態時重新計算",
            "加入進場當下K線開盤已越過停損時取消進場",
            "1、2、3小時無進展仍只記錄，不提前平倉",
        ],
        "規則生效驗收": {
            "K線重新計算成功總數": total_recalc,
            "資料不足放行總數": total_missing,
            "V155新規則實際擋下總數": total_blocked,
            "是否真的生效": "是" if total_blocked > 0 else "否",
        },
        "三年結果": result_rows,
        "跨年摘要": {
            "三年淨利_U": score["三年淨利_USDT"],
            "最差年度淨利_U": score["最差年度淨利_USDT"],
            "正報酬年度數": score["正報酬年度數"],
            "最大年度回撤_pct": score["最大年度DD_pct"],
            "錯誤總數": score["錯誤總數"],
        },
        "限制": [
            "歷史15分鐘K線無法完全重現真實成交延遲、滑價與標記價格順序",
            "進場當下停損有效性只能用該15分鐘K線開盤近似，不能等同真實逐筆成交資料",
        ],
    }

    (OUT / "V155_最終摘要.json").write_text(
        json.dumps(final_report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print("\n" + "=" * 108)
    print("V155 敗單防護｜三年完成")
    print(result_df.to_string(index=False))
    print("\n跨年比較：")
    print(baseline_df.to_string(index=False))
    print(
        f"\n新規則實際擋下：{total_blocked}筆｜"
        f"K線重新計算成功：{total_recalc}筆｜"
        f"資料不足放行：{total_missing}筆"
    )
    print(f"\n輸出資料夾：{OUT}")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
