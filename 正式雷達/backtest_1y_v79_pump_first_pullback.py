#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CryptoRadar V79 - 暴漲後第一次小回踩做多研究

只讀既有 backtest_cache_1y，不下載資料。
所有判斷只使用當時已收完的 K 線，訊號後一根開盤進場。
分別測試 TP1=3R、TP1=4R；同根同時碰停損與停利時，保守算停損。
"""

from __future__ import annotations

import gzip
import json
import math
import pickle
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


CACHE = Path(r"C:\Users\Ting1\CryptoRadar\backtest_cache_1y")
OUT = Path(__file__).with_name("backtest_1y_v79_pump_first_pullback_results")
OUT.mkdir(parents=True, exist_ok=True)

FEE_SLIPPAGE_PER_SIDE = 0.0008918446964558166
RISK_USDT = 10.0
MAX_POSITIONS = 5
MAX_HOLD_BARS = 96 * 7  # 最多觀察 7 天，只是回測結算規則


@dataclass(frozen=True)
class Spec:
    name: str
    pump_8bar: float
    volume_ratio: float
    max_wait: int
    depth_min: float
    depth_max: float
    require_breakout_hold: bool
    adaptive: bool = False


SPECS = (
    Spec("平衡型", 0.06, 1.8, 12, 0.12, 0.58, False),
    Spec("嚴格型", 0.08, 2.3, 10, 0.15, 0.50, True),
    # 不用全市場統一漲幅；判斷該幣自己的 8 小時前 2% 強勢波動。
    Spec("個幣自適應", 0.012, 1.5, 12, 0.12, 0.55, True, True),
)


def load_cache(path: Path):
    try:
        with gzip.open(path, "rb") as fh:
            obj = pickle.load(fh)
    except OSError:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    if not isinstance(obj, tuple) or len(obj) < 2:
        raise ValueError("快取格式不是 (K線, metadata)")
    frame, meta = obj[0], obj[1]
    if not isinstance(frame, pd.DataFrame) or not isinstance(meta, dict):
        raise ValueError("快取內容格式錯誤")
    return frame, meta


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df[["open", "high", "low", "close", "volume"]].copy()
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.replace([np.inf, -np.inf], np.nan).dropna()
    d = d[(d[["open", "high", "low", "close"]] > 0).all(axis=1)]
    d = d[~d.index.duplicated(keep="last")].sort_index()
    if len(d) < 300:
        return pd.DataFrame()

    prev = d["close"].shift(1)
    tr = pd.concat(
        [d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    d["atr"] = tr.rolling(14, min_periods=14).mean()
    for n in (5, 10, 20, 50):
        d[f"ema{n}"] = d["close"].ewm(span=n, adjust=False).mean()
    d["prior_high32"] = d["high"].rolling(32, min_periods=32).max().shift(1)
    d["prior_close8"] = d["close"].shift(8)
    d["pump8"] = d["close"] / d["prior_close8"] - 1.0
    # 嚴格因果：門檻只由訊號前已完成的 30 天資料建立。
    d["pump_q98"] = (
        d["pump8"]
        .rolling(96 * 30, min_periods=96 * 7)
        .quantile(0.98)
        .shift(1)
    )
    d["vol_med20"] = d["volume"].rolling(20, min_periods=20).median().shift(1)
    d["vol_ratio"] = d["volume"] / d["vol_med20"].replace(0, np.nan)
    return d


def generate_candidates(d: pd.DataFrame, meta: dict, spec: Spec):
    rows = []
    rejection = Counter()
    pump_threshold = (
        d["pump_q98"].clip(lower=spec.pump_8bar)
        if spec.adaptive
        else spec.pump_8bar
    )
    impulse_mask = (
        (d["pump8"] >= pump_threshold)
        & (d["close"] > d["prior_high32"])
        & (d["vol_ratio"] >= spec.volume_ratio)
        & (d["close"] > d["ema20"])
        & (d["ema20"] > d["ema50"])
    )
    impulse_idx = np.flatnonzero(impulse_mask.fillna(False).to_numpy())
    last_impulse = -10_000
    last_entry = -10_000

    for i in impulse_idx:
        # 同一段暴漲只保留第一根真正突破，避免一段行情重複觸發。
        if i - last_impulse <= 16:
            rejection["同一段暴漲重複"] += 1
            continue
        last_impulse = i
        if i + 2 >= len(d):
            continue

        start_price = float(d["prior_close8"].iloc[i])
        breakout = float(d["prior_high32"].iloc[i])
        impulse_peak = float(d["high"].iloc[i])
        impulse_move = impulse_peak - start_price
        if not math.isfinite(impulse_move) or impulse_move <= 0:
            continue

        touched = False
        found = False
        search_end = min(len(d) - 2, i + spec.max_wait)
        peak = impulse_peak
        for j in range(i + 1, search_end + 1):
            peak = max(peak, float(d["high"].iloc[j]))
            low = float(d["low"].iloc[j])
            close = float(d["close"].iloc[j])
            ema5 = float(d["ema5"].iloc[j])
            ema10 = float(d["ema10"].iloc[j])
            depth = (peak - low) / max(peak - start_price, 1e-12)

            if low <= ema10 * 1.004 or close < ema5:
                touched = True
            if not touched:
                continue
            if depth < spec.depth_min:
                continue
            if depth > spec.depth_max:
                rejection["回踩過深"] += 1
                break
            if spec.require_breakout_hold and close < breakout:
                rejection["嚴格型跌回突破位下"] += 1
                continue

            prev_high = float(d["high"].iloc[j - 1])
            reclaim = (
                close > ema5
                and close > prev_high
                and close > float(d["open"].iloc[j])
            )
            if not reclaim:
                continue

            e = j + 1
            if e - last_entry < 32:
                rejection["進場冷卻"] += 1
                break
            entry = float(d["open"].iloc[e])
            pull_low = float(d["low"].iloc[i + 1 : j + 1].min())
            atr = float(d["atr"].iloc[j])
            stop = pull_low - 0.15 * atr
            risk = entry - stop
            stop_pct = risk / entry
            if not (0.006 <= stop_pct <= 0.12):
                rejection["停損距離不合理"] += 1
                break
            # 防止訊號確認後隔根直接大幅跳高追價。
            if entry > close + 0.25 * atr:
                rejection["隔根跳空追價"] += 1
                break

            score = (
                min(float(d["pump8"].iloc[i]), 0.30) * 100
                + min(float(d["vol_ratio"].iloc[i]), 8.0) * 2
                - depth * 8
                - (e - i) * 0.15
            )
            rows.append(
                {
                    "spec": spec.name,
                    "exchange": str(meta.get("exchange", "")),
                    "symbol": str(meta.get("symbol", "")),
                    "base": str(meta.get("base", "")),
                    "signal_time": d.index[j],
                    "entry_time": d.index[e],
                    "entry_index": e,
                    "entry": entry,
                    "stop": stop,
                    "risk_price": risk,
                    "stop_pct": stop_pct,
                    "pump_time": d.index[i],
                    "pump_8bar_pct": float(d["pump8"].iloc[i]) * 100,
                    "adaptive_pump_threshold_pct": (
                        float(d["pump_q98"].iloc[i]) * 100
                        if math.isfinite(float(d["pump_q98"].iloc[i]))
                        else float("nan")
                    ),
                    "pump_volume_ratio": float(d["vol_ratio"].iloc[i]),
                    "pullback_depth_pct": depth * 100,
                    "wait_bars": e - i,
                    "score": score,
                    "cache_file": str(meta.get("cache_file", "")),
                    "_frame": d,
                }
            )
            last_entry = e
            found = True
            break

        if not found:
            rejection["暴漲後未形成合格第一次回踩"] += 1
    return rows, rejection, len(impulse_idx)


def simulate(c: dict, rr: float):
    d = c["_frame"]
    e = int(c["entry_index"])
    entry = float(c["entry"])
    stop = float(c["stop"])
    risk = entry - stop
    tp1 = entry + rr * risk
    end = min(len(d) - 1, e + MAX_HOLD_BARS)
    exit_i = end
    reason = "資料結束/觀察期結束"
    exit_price = float(d["close"].iloc[end])
    outcome = "未平"
    for k in range(e, end + 1):
        hit_stop = float(d["low"].iloc[k]) <= stop
        hit_tp = float(d["high"].iloc[k]) >= tp1
        if hit_stop:  # 同 K 雙碰，保守算停損
            exit_i, exit_price, reason, outcome = k, stop, "停損", "敗"
            break
        if hit_tp:
            exit_i, exit_price, reason, outcome = k, tp1, f"TP1_{rr:g}R", "勝"
            break
    gross_r = (exit_price - entry) / risk
    trading_cost = (entry + exit_price) * FEE_SLIPPAGE_PER_SIDE
    net_r = gross_r - trading_cost / risk
    return {
        **{k: v for k, v in c.items() if not k.startswith("_") and k != "entry_index"},
        "rr": rr,
        "tp1": tp1,
        "tp2": entry + max(rr + 1.0, 5.0) * risk,
        "tp3": entry + max(rr + 2.0, 6.0) * risk,
        "exit_time": d.index[exit_i],
        "exit_price": exit_price,
        "exit_reason": reason,
        "outcome": outcome,
        "gross_r": gross_r,
        "net_r": net_r,
        "profit_usdt": net_r * RISK_USDT,
    }


def portfolio_filter(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    d = trades.sort_values(["entry_time", "score"], ascending=[True, False]).copy()
    chosen = []
    active = []
    last_base_time = {}
    for _, r in d.iterrows():
        t = pd.Timestamp(r["entry_time"])
        active = [x for x in active if pd.Timestamp(x[0]) > t]
        base = str(r["base"])
        if any(x[1] == base for x in active):
            continue
        prev = last_base_time.get(base)
        if prev is not None and (t - prev).total_seconds() < 24 * 3600:
            continue
        if len(active) >= MAX_POSITIONS:
            continue
        chosen.append(r.to_dict())
        active.append((r["exit_time"], base))
        last_base_time[base] = t
    return pd.DataFrame(chosen)


def metrics(df: pd.DataFrame, label: str):
    closed = df[df["outcome"].isin(["勝", "敗"])] if not df.empty else df
    wins = int((closed["outcome"] == "勝").sum()) if not closed.empty else 0
    losses = int((closed["outcome"] == "敗").sum()) if not closed.empty else 0
    net = float(closed["profit_usdt"].sum()) if not closed.empty else 0.0
    curve = closed.sort_values("exit_time")["profit_usdt"].cumsum() + 1000 if not closed.empty else pd.Series([1000.0])
    peak = curve.cummax()
    dd = float(((peak - curve) / peak * 100).max()) if len(curve) else 0.0
    return {
        "版本": label,
        "交易數": int(len(df)),
        "勝": wins,
        "敗": losses,
        "未平": int(len(df) - wins - losses),
        "勝率_pct": round(wins / max(wins + losses, 1) * 100, 2),
        "平均R": round(float(closed["net_r"].mean()), 3) if not closed.empty else 0.0,
        "淨利_USDT": round(net, 2),
        "最大回撤_pct": round(dd, 2),
    }


def main() -> int:
    files = sorted(CACHE.glob("*.pkl.gz"))
    if not files:
        raise SystemExit(f"找不到快取：{CACHE}")
    print(f"V79 暴漲第一次回踩回測｜既有快取 {len(files)} 個市場", flush=True)
    all_candidates = {s.name: [] for s in SPECS}
    rejects = {s.name: Counter() for s in SPECS}
    impulses = Counter()
    errors = []

    for n, path in enumerate(files, 1):
        try:
            frame, meta = load_cache(path)
            meta = dict(meta)
            meta["cache_file"] = path.name
            d = indicators(frame)
            if d.empty:
                continue
            for spec in SPECS:
                rows, rej, count = generate_candidates(d, meta, spec)
                all_candidates[spec.name].extend(rows)
                rejects[spec.name].update(rej)
                impulses[spec.name] += count
        except Exception as exc:
            errors.append({"file": path.name, "error": repr(exc)})
        if n == 1 or n % 25 == 0 or n == len(files):
            print(f"\r掃描中 {n}/{len(files)}", end="", flush=True)
    print()

    comparisons = []
    audit_rows = []
    for spec in SPECS:
        raw = all_candidates[spec.name]
        # 同一幣、同一時刻在多交易所出現，只留分數最高者。
        slim = []
        for c in raw:
            x = {k: v for k, v in c.items() if k != "_frame"}
            audit_rows.append(x)
        for rr in (3.0, 4.0):
            simulated = pd.DataFrame(simulate(c, rr) for c in raw)
            if not simulated.empty:
                simulated["bucket"] = pd.to_datetime(simulated["entry_time"], utc=True).dt.floor("15min")
                simulated = simulated.sort_values("score", ascending=False).drop_duplicates(["base", "bucket"])
            selected = portfolio_filter(simulated)
            label = f"{spec.name}_TP1_{rr:g}R"
            selected.to_csv(OUT / f"{label}_所有交易.csv", index=False, encoding="utf-8-sig")
            comparisons.append(metrics(selected, label))
            btc = selected[selected["base"].astype(str).str.upper().eq("BTC")] if not selected.empty else selected
            btc.to_csv(OUT / f"{label}_BTC交易.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame(comparisons).to_csv(OUT / "版本比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(audit_rows).to_csv(OUT / "全部型態候選_去除K線物件.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(OUT / "讀取錯誤.csv", index=False, encoding="utf-8-sig")
    funnel = {
        "cache_markets": len(files),
        "errors": len(errors),
        "impulses": dict(impulses),
        "raw_candidates": {k: len(v) for k, v in all_candidates.items()},
        "rejections": {k: dict(v) for k, v in rejects.items()},
        "rules": {
            "entry": "暴漲突破後第一次回踩，收盤重新站上EMA5且突破前一根高點，下一根開盤",
            "same_bar": "停損與停利同根發生時算停損",
            "cost_per_side": FEE_SLIPPAGE_PER_SIDE,
            "risk_usdt": RISK_USDT,
            "max_positions": MAX_POSITIONS,
            "btc_market_filter": False,
        },
    }
    (OUT / "訊號漏斗.json").write_text(json.dumps(funnel, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    comp = pd.DataFrame(comparisons)
    print("\n" + "=" * 92)
    print("V79 暴漲後第一次小回踩做多｜結果")
    print("=" * 92)
    print(comp.to_string(index=False))
    print(f"\n結果資料夾：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
