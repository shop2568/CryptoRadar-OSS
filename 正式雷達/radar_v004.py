#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V004：把 V101 MAX2 乾淨加斜率策略正式搬到三交易所雷達。"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import time
import re
from pathlib import Path

import pandas as pd

import radar_v003 as common
import v101_live_engine as engine
import v101_live_policy as policy
from live_v004 import LiveTrader


ROOT = Path(__file__).resolve().parent
STATE = Path(os.getenv("STATE_FILE", str(ROOT / "radar_v004_live_state.json")))
SCAN = int(os.getenv("SCAN_INTERVAL", "300"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "86400"))
SHADOW = os.getenv("V004_SHADOW", "0").strip().lower() in {"1", "true", "yes", "on"}
RUN_ONCE = os.getenv("V004_ONCE", "0").strip().lower() in {"1", "true", "yes", "on"}
SHADOW_LIMIT = int(os.getenv("V004_SHADOW_LIMIT", "80"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("V004")


def load_state():
    blank = {"signals": {}, "positions": {}, "history": []}
    if not STATE.exists(): return blank
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict): blank.update(data)
    except Exception: log.exception("V004 state load failed")
    return blank


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(STATE)


def zhside(side):
    return "做多" if side == "LONG" else "做空"


def chinese_reason(error):
    """將實盤層、交易所與網路常見錯誤轉成使用者看得懂的中文。"""
    text = str(error).strip()
    exact = {
        "live trading flag missing": "實盤開關尚未開啟",
        "live trading kill switch is off": "實盤開關尚未開啟",
        "no available USDT futures balance": "幣安合約帳戶沒有可用的 USDT 餘額",
        "hedge mode is not supported; use one-way mode": "幣安目前為雙向持倉模式，V004 需要單向持倉模式",
        "invalid live risk": "目前價格與停損位置不合理，無法安全計算倉位",
        "price chased above allowed range": "目前價格已高於可接受進場範圍，不追高",
        "price chased below allowed range": "目前價格已低於可接受進場範圍，不追空",
        "position below exchange minimum": "計算出的下單金額低於幣安最低下單限制",
        "maximum five positions reached": "目前已滿五倉",
        "same asset already has a position": "同標的已有持倉，不重複進場",
        "protection order failed; position closed": "停損或停利保護單建立失敗，部位已立即平倉",
    }
    if text in exact:
        return exact[text]
    patterns = (
        (r"^live RR below ([0-9.]+)R \(([0-9.\-]+)\)$", r"目前價格進場後的 TP1 報酬比只剩 \2R，低於最低 \1R"),
        (r"^actual fill RR below ([0-9.]+)R \(([0-9.\-]+)\); position closed$", r"實際成交後 TP1 報酬比只剩 \2R，低於最低 \1R，已立即平倉"),
        (r"^daily loss lock ([0-9.]+)%$", r"當日虧損已達 \1%，風險保護暫停新增部位"),
    )
    for pattern, replacement in patterns:
        if re.match(pattern, text, flags=re.I):
            return re.sub(pattern, replacement, text, flags=re.I)
    lowered = text.lower()
    if "insufficient margin" in lowered or "margin is insufficient" in lowered:
        return "幣安可用保證金不足"
    if "timeout" in lowered or "timed out" in lowered:
        return "連線逾時，本輪未進場，下一輪會重新掃描"
    if "rate limit" in lowered or "too many requests" in lowered:
        return "交易所請求過多而暫時限制，本輪未進場，下一輪會重試"
    if "network" in lowered or "connection" in lowered:
        return "交易所連線異常，本輪未進場，下一輪會重試"
    return f"未能完成下單（系統原始訊息：{text}）"


def signal_message(row):
    fr = "無資料" if row.get("funding") is None else f'{row["funding"]*100:+.4f}%'
    weights = f'{row["tp1_weight"]*100:.0f}%／{row["tp2_weight"]*100:.0f}%／{row["tp3_weight"]*100:.0f}%／尾單{row["runner_weight"]*100:.0f}%'
    kind = "原核心" if row.get("portfolio_kind") == "CORE" else "新增強勢模組"
    return (
        "📡 CryptoRadar V004｜已實盤進場\n"
        f'類型：{kind}\n訊號來源：{row.get("venue", "Binance")}\n實盤下單：Binance\n'
        f'標的：{row["base"]}USDT\n方向：{zhside(row["direction"])}\n'
        f'實際進場：{row["actual_entry"]:.10g}\n數量：{row["quantity"]:.10g}｜全倉 3x｜單筆風險 1%\n'
        f'停損：{row["stop"]:.10g}\nTP1：{row["tp1"]:.10g}｜{row["actual_rr"]:.2f}R\n'
        f'TP2：{row["tp2"]:.10g}\nTP3：{row["tp3"]:.10g}\n資金費率：{fr}\n'
        f'分批：{weights}\nTP1 後剩餘部位移到成本保護，再依 TP2、TP3 與該標的 5／10 日線退出。'
    )


def _active_for_policy(state, snapshot):
    rows = list(state.get("positions", {}).values())
    known = {str(x.get("base", "")).upper() for x in rows}
    for pos in snapshot.get("positions", []):
        base = common.base(str(pos.get("symbol") or ""))
        if not base or base in known: continue
        side = "LONG" if common.f((pos.get("info") or {}).get("positionAmt")) > 0 else "SHORT"
        rows.append({"base": base, "direction": side, "portfolio_kind": "CORE", "open_time": 0, "external": True})
    return rows


def main():
    global STATE
    if SHADOW and os.getenv("STATE_FILE") is None:
        STATE = ROOT / "radar_v004_shadow_state.json"
    lock = (ROOT / ("radar_v004_shadow.lock" if SHADOW else "radar_v004.lock")).open("w")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise RuntimeError("CryptoRadar V004 已在執行")

    trade = common.client(); scanners = common.scan_clients(trade)
    trade_map = common.binance_trade_map(trade); trader = LiveTrader(trade, ROOT)
    state = load_state(); stop = False; pool = []
    account = trader.account() if SHADOW else trader.guard()
    def halt(*_):
        nonlocal stop; stop = True
    signal.signal(signal.SIGINT, halt); signal.signal(signal.SIGTERM, halt)
    snap = trader.snapshot()
    if not SHADOW:
        common.tg(
            "📡 CryptoRadar V004 實盤已啟動\n"
            "掃描：Binance、Bitget、BingX；相同標的合併後只下 Binance。\n"
            "策略：V101 MAX2 乾淨加斜率｜原核心優先｜新增模組最多2倉｜TP1最低4R。\n"
            "風控：全倉3倍｜單筆完整風險1%｜最多5倉｜不設每日停開倉。\n"
            "出場：TP1分批、成本保護、TP2、TP3、5／10日線尾單。\n"
            f"啟動前已有持倉：{snap['count']}倉（V004不接管舊單）。"
        )
    else: log.info("V004 SHADOW mode; no orders and no Telegram")

    while not stop:
        started = time.time()
        try:
            if not pool:
                targets, errors = engine.discover_targets(scanners)
                pool = targets
                for target in targets:
                    if target.exchange_name == "Binance": trade_map.setdefault(str(target.base).upper(), target.symbol)
                counts = {name: sum(x.exchange_name == name for x in targets) for name, _ in scanners}
                log.info("V004 merged market pool %d counts=%s discovery_errors=%d", len(pool), counts, len(errors))
                if SHADOW and RUN_ONCE and SHADOW_LIMIT > 0:
                    pool = pool[:SHADOW_LIMIT]
                    log.info("V004 shadow validation sample=%d; live mode remains full market pool", len(pool))
            if not SHADOW: trader.reconcile(state, common.tg, zhside, save_state)
            snap = trader.snapshot(); active = _active_for_policy(state, snap)
            active_bases = {str(x.get("base", "")).upper() for x in active}
            candidates = []; scan_errors = []
            for index, target in enumerate(pool, 1):
                if stop: break
                if str(target.base).upper() in active_bases: continue
                try:
                    raw = common.frame(target.exchange, target.symbol)
                    for row in engine.causal_candidates(target, raw):
                        row["venue"] = target.exchange_name; row["source_symbol"] = target.symbol
                        trade_symbol = trade_map.get(str(row["base"]).upper())
                        if not trade_symbol: continue
                        if target.exchange_name != "Binance": row = common.translate_to_binance(row, trade_symbol, trade)
                        key = f'{row["base"]}:{row["direction"]}:{row["portfolio_kind"]}'
                        if time.time() - common.f(state["signals"].get(key)) >= COOLDOWN:
                            candidates.append(row)
                except Exception as exc:
                    if len(scan_errors) < 50:
                        scan_errors.append({"venue": target.exchange_name, "symbol": target.symbol, "error": f"{type(exc).__name__}: {exc}"})
                    log.debug("scan %s failed: %s", target.symbol, exc)
                if index % 10 == 0 or index == len(pool):
                    print(f"\r三大交易所掃描中  {index}/{len(pool)}", end="", flush=True)
            print(flush=True)
            selected, rejected, _ = policy.select(candidates, active, state.get("history", []), time.time())
            if SHADOW:
                audit = {"time": pd.Timestamp.now(tz="UTC").isoformat(), "markets": len(pool),
                         "raw_candidates": candidates, "selected": selected, "rejected": rejected,
                         "scan_errors": scan_errors, "account": account}
                (ROOT / "v004_shadow_result.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                log.info("V004 shadow complete markets=%d candidates=%d selected=%d", len(pool), len(candidates), len(selected))
                if RUN_ONCE: break
                selected = []
            for row in selected:
                row["funding"] = common.funding(trade, row["symbol"]); row["status"] = "pending"; row["open_time"] = time.time()
                row["slot"] = "原核心優先倉" if row["portfolio_kind"] == "CORE" else "新增模組倉"
                key = f'{row["base"]}:{row["direction"]}:{row["portfolio_kind"]}'
                state["pending"] = {"key": key, "symbol": row["symbol"], "time": time.time()}; save_state(state)
                try:
                    row.update(trader.enter(row, state)); row["status"] = "open"
                    state["signals"][key] = time.time(); state["positions"][key] = row; state.pop("pending", None)
                    save_state(state); common.tg(signal_message(row))
                    log.info("V004 live accepted %s %s %s", row["base"], row["portfolio_kind"], row["direction"])
                except Exception as exc:
                    state.pop("pending", None); save_state(state)
                    common.tg(f'⚠️ V004 未進場\n標的：{row["base"]}USDT\n原因：{chinese_reason(exc)}')
                    log.warning("V004 live rejected %s: %s", row["base"], exc)
            elapsed = time.time() - started
            if selected:
                log.info("V004 本輪掃描完成 markets=%d selected=%d elapsed=%.1fs", len(pool), len(selected), elapsed)
            else:
                log.info("V004 本輪掃描完成 markets=%d 無新訊號 elapsed=%.1fs", len(pool), elapsed)
        except Exception: log.exception("V004 cycle failed")
        remain = max(0, SCAN - (time.time() - started))
        while remain > 0 and not stop:
            wait = min(1, remain); time.sleep(wait); remain -= wait
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
