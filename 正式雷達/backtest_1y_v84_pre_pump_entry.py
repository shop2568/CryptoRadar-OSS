#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V84：暴漲前布局的因果回測。

目的：不等 8 根大漲完成才追價；在壓縮結束、量能剛增加、價格剛脫離短箱體時進場。
所有進場判斷只使用訊號當下與更早的資料，下一根 15 分鐘 K 線開盤成交。
未來暴漲標籤只用於事後稽核，不參與選單、排名或進場。
正式 V72.8S 核心不變，本檔只做影子研究；TP1 固定最低 4R。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_1y_v79_pump_first_pullback as core
import backtest_1y_v81_pump_regime_filter as v81
import backtest_1y_v83_pump_entry_ab as v83


OUT = Path(__file__).with_name("backtest_1y_v84_pre_pump_entry_results")
OUT.mkdir(parents=True, exist_ok=True)
RR = 4.0
LOOKAHEAD_AUDIT = 32  # 8 小時；僅標記是否成功早於暴漲，絕不參與進場。


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    d = v81.prepare(frame)
    if d.empty:
        return d
    atr = d["atr"].replace(0, np.nan)
    span = (d["high"] - d["low"]).replace(0, np.nan)
    d["prior_high20"] = d["high"].rolling(20, min_periods=20).max().shift(1)
    d["prior_low20"] = d["low"].rolling(20, min_periods=20).min().shift(1)
    d["prior_high8"] = d["high"].rolling(8, min_periods=8).max().shift(1)
    d["prior_low8"] = d["low"].rolling(8, min_periods=8).min().shift(1)
    d["range20_atr"] = (d["prior_high20"] - d["prior_low20"]) / atr
    d["range8_atr"] = (d["prior_high8"] - d["prior_low8"]) / atr
    d["ret4"] = d["close"] / d["close"].shift(4) - 1.0
    d["ret8"] = d["close"] / d["close"].shift(8) - 1.0
    d["ema20_slope8"] = (d["ema20"] - d["ema20"].shift(8)) / atr
    d["body_up_atr"] = (d["close"] - d["open"]) / atr
    d["close_location"] = (d["close"] - d["low"]) / span
    d["volume_fast"] = d["volume"].rolling(3, min_periods=3).mean()
    d["volume_slow"] = d["volume"].rolling(20, min_periods=20).median().shift(1)
    d["volume_build"] = d["volume_fast"] / d["volume_slow"].replace(0, np.nan)
    d["below_ema20_recent"] = (d["close"] < d["ema20"]).rolling(8, min_periods=8).max().shift(1)
    # 事後暴漲稽核使用：未來 8 小時最高價相對訊號收盤漲幅。
    future_high = d["high"].shift(-1)[::-1].rolling(LOOKAHEAD_AUDIT, min_periods=1).max()[::-1]
    d["audit_forward_high_8h"] = future_high
    return d


def _group(base: str) -> str:
    b = base.upper().replace("1000", "")
    return "BTC" if b == "BTC" else "ETH" if b == "ETH" else "小幣"


def _adaptive_limits(group: str) -> dict[str, float]:
    # 大幣本身波動較小，不能拿小幣的固定漲幅與量比硬套。
    if group == "BTC":
        return {"min_ret4": 0.0015, "max_ret8": 0.035, "min_vol": 1.08, "max_range": 5.2}
    if group == "ETH":
        return {"min_ret4": 0.0020, "max_ret8": 0.045, "min_vol": 1.12, "max_range": 5.4}
    return {"min_ret4": 0.0030, "max_ret8": 0.075, "min_vol": 1.20, "max_range": 5.0}


def candidates(d: pd.DataFrame, meta: dict, variant: str) -> tuple[list[dict], Counter]:
    base = str(meta.get("base") or str(meta.get("symbol", "")).split("/")[0])
    group = _group(base)
    lim = _adaptive_limits(group)
    trend = (
        (d["close"] > d["ema20"])
        & (d["ema5"] > d["ema10"])
        & (d["ema20_slope8"] > 0.0)
        & (d["close"] > d["ema320"] * 0.985)
        & (d["ema320_slope16"] > -0.20)
    )
    early_not_pumped = (
        (d["ret4"] >= lim["min_ret4"])
        & (d["ret8"] <= lim["max_ret8"])
        & (d["pump8"] < d["pump_q98"].clip(lower=0.012))
    )
    quality_candle = (
        (d["body_up_atr"] >= 0.18)
        & (d["body_up_atr"] <= 1.30)
        & (d["close_location"] >= 0.64)
        & (d["vol_ratio"] >= lim["min_vol"])
        & (d["vol_ratio"] <= 5.0)
        & (d["volume_build"] >= 1.02)
    )
    if variant == "壓縮早突破":
        setup = (
            (d["close"] > d["prior_high20"])
            & (d["range20_atr"] >= 1.3)
            & (d["range20_atr"] <= lim["max_range"])
        )
    else:  # 均線假跌破站回：抓 BTC/ETH 及強勢小幣第二段啟動。
        setup = (
            (d["below_ema20_recent"] > 0)
            & (d["close"] > d["ema20"])
            & (d["close"] > d["prior_high8"])
            & (d["range8_atr"] <= 4.5)
        )
    mask = (trend & early_not_pumped & quality_candle & setup).fillna(False)
    idxs = np.flatnonzero(mask.to_numpy())
    rows: list[dict] = []
    rejected = Counter()
    last_entry = -99999
    for j0 in idxs:
        j = int(j0)
        e = j + 1
        if e >= len(d):
            continue
        if e - last_entry < 32:
            rejected["同幣8小時冷卻"] += 1
            continue
        entry = float(d["open"].iloc[e])
        close = float(d["close"].iloc[j])
        atr = float(d["atr"].iloc[j])
        if min(entry, close, atr) <= 0 or not all(math.isfinite(x) for x in (entry, close, atr)):
            rejected["價格資料異常"] += 1
            continue
        if entry > close + 0.20 * atr:
            rejected["隔根跳高追價"] += 1
            continue
        lookback = 20 if variant == "壓縮早突破" else 12
        structural_low = float(d["low"].iloc[max(0, j - lookback + 1) : j + 1].min())
        stop = structural_low - 0.12 * atr
        risk = entry - stop
        stop_pct = risk / entry
        min_stop = 0.004 if group in {"BTC", "ETH"} else 0.006
        if not (min_stop <= stop_pct <= 0.055):
            rejected["停損距離不合理"] += 1
            continue
        # 真實 4R 空間：已知舊高若在上方，至少須留出 4R；創高則視為無已知壓力。
        old_high = float(d["old_high30d"].iloc[j])
        room_r = float("inf") if not math.isfinite(old_high) or old_high <= entry else (old_high - entry) / risk
        if room_r < RR:
            rejected["舊壓力前不足4R"] += 1
            continue
        pump_threshold = max(float(d["pump_q98"].iloc[j]), 0.012)
        forward_high = float(d["audit_forward_high_8h"].iloc[j])
        future_gain = forward_high / close - 1.0 if math.isfinite(forward_high) else float("nan")
        # 稽核門檻按該幣自身歷史波動；此欄絕不進入 score。
        audit_pump = bool(math.isfinite(future_gain) and future_gain >= pump_threshold)
        score = (
            (18.0 if variant == "壓縮早突破" else 14.0)
            + min(float(d["volume_build"].iloc[j]), 3.0) * 8.0
            + min(float(d["vol_ratio"].iloc[j]), 3.0) * 4.0
            + min(max(float(d["ema20_slope8"].iloc[j]), 0.0), 2.0) * 4.0
            + float(d["close_location"].iloc[j]) * 5.0
            - max(float(d["range20_atr"].iloc[j]) - 3.0, 0.0) * 2.0
            - max(float(d["ret8"].iloc[j]) - 0.025, 0.0) * 100.0
        )
        rows.append({
            "version": "V84", "entry_mode": variant,
            "exchange": str(meta.get("exchange", "")), "symbol": str(meta.get("symbol", "")),
            "base": base, "asset_group": group,
            "signal_time": d.index[j], "entry_time": d.index[e], "entry_index": e,
            "entry": entry, "stop": stop, "risk_price": risk, "stop_pct": stop_pct,
            "ret4_before_entry_pct": float(d["ret4"].iloc[j]) * 100,
            "ret8_before_entry_pct": float(d["ret8"].iloc[j]) * 100,
            "volume_ratio": float(d["vol_ratio"].iloc[j]),
            "volume_build_3v20": float(d["volume_build"].iloc[j]),
            "range20_atr": float(d["range20_atr"].iloc[j]),
            "ema20_slope8_atr": float(d["ema20_slope8"].iloc[j]),
            "prior_resistance_room_r": room_r,
            "score": score,
            "audit_future_8h_gain_pct": future_gain * 100,
            "audit_became_own_pump_within_8h": audit_pump,
            "audit_own_pump_threshold_pct": pump_threshold * 100,
            "_frame": d,
        })
        last_entry = e
    return rows, rejected


def dedupe_and_simulate(rows: list[dict]) -> pd.DataFrame:
    sim = pd.DataFrame(core.simulate(x, RR) for x in rows)
    if sim.empty:
        return sim
    sim["bucket"] = pd.to_datetime(sim["entry_time"], utc=True).dt.floor("15min")
    sim = sim.sort_values(["score", "entry_time"], ascending=[False, True])
    sim = sim.drop_duplicates(["base", "bucket"], keep="first")
    return core.portfolio_filter(sim)


def metrics_by_group(df: pd.DataFrame, version: str) -> list[dict]:
    out = [core.metrics(df, version)]
    out[0]["資產組"] = "全部"
    for group in ("BTC", "ETH", "小幣"):
        x = df[df["asset_group"].eq(group)] if not df.empty else df
        m = core.metrics(x, version)
        m["資產組"] = group
        out.append(m)
    for m in out:
        scope = df if m["資產組"] == "全部" else df[df["asset_group"].eq(m["資產組"])]
        m["噴出前命中數"] = int(scope.get("audit_became_own_pump_within_8h", pd.Series(dtype=bool)).fillna(False).sum())
        m["噴出前命中率_pct"] = round(m["噴出前命中數"] / max(len(scope), 1) * 100, 2)
    return out


def main() -> int:
    markets, errors = v83.market_frames()
    all_rows: dict[str, list[dict]] = {"壓縮早突破": [], "均線假跌破站回": [], "合併版": []}
    rejects: dict[str, Counter] = {"壓縮早突破": Counter(), "均線假跌破站回": Counter()}
    print(f"V84 暴漲前布局｜去重市場 {len(markets)}｜TP1 固定 4R", flush=True)
    for n, (frame, meta) in enumerate(markets, 1):
        try:
            d = prepare(frame)
            if d.empty:
                continue
            for variant in ("壓縮早突破", "均線假跌破站回"):
                found, rejected = candidates(d, meta, variant)
                all_rows[variant].extend(found)
                rejects[variant].update(rejected)
        except Exception as exc:
            errors.append({"exchange": meta.get("exchange"), "symbol": meta.get("symbol"), "error": repr(exc)})
        if n == 1 or n % 25 == 0 or n == len(markets):
            print(f"\r掃描市場 {n}/{len(markets)}", end="", flush=True)
    print()
    all_rows["合併版"] = all_rows["壓縮早突破"] + all_rows["均線假跌破站回"]

    comparisons: list[dict] = []
    for name, rows in all_rows.items():
        chosen = dedupe_and_simulate(rows)
        chosen.to_csv(OUT / f"V84_{name}_全部交易.csv", index=False, encoding="utf-8-sig")
        focus = chosen[chosen["asset_group"].isin(["BTC", "ETH"])] if not chosen.empty else chosen
        focus.to_csv(OUT / f"V84_{name}_BTC_ETH交易.csv", index=False, encoding="utf-8-sig")
        hits = chosen[chosen["audit_became_own_pump_within_8h"].fillna(False)] if not chosen.empty else chosen
        hits.to_csv(OUT / f"V84_{name}_真正噴出前命中.csv", index=False, encoding="utf-8-sig")
        comparisons.extend(metrics_by_group(chosen, f"V84_{name}_4R"))

    comp = pd.DataFrame(comparisons)
    comp.to_csv(OUT / "V84版本與資產分組比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(OUT / "V84讀取錯誤.csv", index=False, encoding="utf-8-sig")
    audit = {
        "version": "V84_PRE_PUMP_CAUSAL",
        "unique_markets": len(markets),
        "raw_candidates": {k: len(v) for k, v in all_rows.items()},
        "rejections": {k: dict(v) for k, v in rejects.items()},
        "errors": len(errors),
        "tp1_rr": RR,
        "entry_uses_future": False,
        "future_pump_label_is_audit_only": True,
        "formal_core_changed": False,
        "auto_trade_enabled": False,
    }
    (OUT / "V84訊號漏斗與防偷看稽核.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 110)
    print("V84 暴漲前布局回測結果")
    print("=" * 110)
    print(comp.to_string(index=False))
    print(f"\n結果資料夾：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
