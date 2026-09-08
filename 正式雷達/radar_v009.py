#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V009：V008防當機版 + 美股休市可買 + Alpaca量能軟排序。"""

from __future__ import annotations

import ctypes
import fcntl
import gc
import json
import logging
import os
import queue
import re
import signal
import threading
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd


__version__ = "V009"

# 沿用 V008 防當機與無損計算層；V157 交易策略不變。
os.environ["V007_HEARTBEAT"] = os.getenv("V009_HEARTBEAT", "/run/cryptoradar-v009/heartbeat")
os.environ["V007_RUNTIME"] = os.getenv("V009_RUNTIME", "/run/cryptoradar-v009")
os.environ["V006_SHADOW"] = os.getenv("V009_SHADOW", "0")
os.environ["V006_ONCE"] = os.getenv("V009_ONCE", "0")
os.environ["V006_SHADOW_LIMIT"] = os.getenv("V009_SHADOW_LIMIT", "80")

import radar_v007 as previous_version
import alpaca_volume_gate_v009
import v008_lossless_engine


app = previous_version.app
app.engine = v008_lossless_engine


class _V009LogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return re.sub(r"V00[3-8](?:\.\d+)?", "V009", str(msg)), kwargs


app.log = _V009LogAdapter(logging.getLogger("V009"), {})
_previous_tg = app.common.tg


def _v009_tg(message):
    text = re.sub(r"V00[3-8](?:\.\d+)?", "V009", str(message))
    text = text.replace(
        "V009三大交易所合併市場｜V157頂級救援",
        "V009三大交易所加速掃描｜V157頂級救援",
    )
    return _previous_tg(text)


app.common.tg = _v009_tg

_previous_signal_message = app.signal_message


def _v009_signal_message(row):
    text = _previous_signal_message(row)
    if row.get("美股代幣"):
        text += (
            f'\n美股代幣原股：{row.get("Alpaca原股", "-")}'
            f'\nAlpaca量能：{row.get("Alpaca量能確認", "未確認")}'
        )
        latest = row.get("Alpaca最新量能倍數")
        build = row.get("Alpaca近3根量能倍數")
        if latest is not None and build is not None:
            try:
                text += f'（最新{float(latest):.2f}倍／近3根{float(build):.2f}倍）'
            except (TypeError, ValueError):
                pass
    return text


app.signal_message = _v009_signal_message


_RUNTIME = Path(os.getenv("V009_RUNTIME", "/run/cryptoradar-v009"))
_HEARTBEAT = Path(os.getenv("V009_HEARTBEAT", str(_RUNTIME / "heartbeat")))
_CYCLE_COMPLETE = _RUNTIME / "cycle_complete"
_STARTUP_STATE = app.ROOT / "radar_v009_startup_notice.json"
_NOTIFY_STARTUP = os.getenv("V009_NOTIFY_STARTUP", "0") == "1"


def _touch_health(cycle_complete=False):
    try:
        _HEARTBEAT.touch(exist_ok=True)
        if cycle_complete:
            _CYCLE_COMPLETE.touch(exist_ok=True)
    except OSError:
        pass


def _release_unused_memory():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _startup_notice_due(now=None):
    """每個正式版本只在第一次啟動通知一次。"""
    try:
        old = json.loads(_STARTUP_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        old = {}
    return str(old.get("version") or "") != __version__


def _remember_startup_notice(now=None):
    now = float(now or time.time())
    temporary = _STARTUP_STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": __version__, "sent_at": now}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(_STARTUP_STATE)


def _scan_parallel(pool, active_bases, trade, trade_map, state, stop_check):
    """各交易所一條抓取流；正式引擎仍由主執行緒逐筆運算。

    - 同交易所不併發，保留 ccxt rate limiter。
    - K 線抓取可跨 Binance / Bitget / BingX 同時進行。
    - 訊號運算保持單執行緒，避免舊引擎全域稽核資料競爭。
    - 最後按原 pool index 排序，排倉輸入順序與 V007 相同。
    """
    grouped = defaultdict(list)
    for index, target in enumerate(pool):
        if str(target.base).upper() not in active_bases:
            grouped[target.exchange_name].append((index, target))

    output_queue = queue.Queue(maxsize=12)
    trade_lock = threading.Lock()
    producers = []
    producer_count = len(grouped)

    def producer(items):
        try:
            for index, target in items:
                if stop_check():
                    break
                try:
                    if target.exchange is trade:
                        with trade_lock:
                            raw = app.common.frame(target.exchange, target.symbol)
                    else:
                        raw = app.common.frame(target.exchange, target.symbol)
                    output_queue.put((index, target, raw, None))
                except Exception as exc:
                    output_queue.put((index, target, None, exc))
        finally:
            output_queue.put((None, None, None, None))

    for items in grouped.values():
        thread = threading.Thread(target=producer, args=(items,), daemon=True)
        thread.start()
        producers.append(thread)

    candidates_by_index = []
    scan_errors = []
    completed_producers = 0
    processed = 0
    expected = sum(len(items) for items in grouped.values())
    while completed_producers < producer_count:
        index, target, raw, error = output_queue.get()
        if index is None:
            completed_producers += 1
            continue
        processed += 1
        if error is not None:
            if len(scan_errors) < 50:
                scan_errors.append({
                    "venue": target.exchange_name,
                    "symbol": target.symbol,
                    "error": f"{type(error).__name__}: {error}",
                })
        else:
            try:
                rows = app.engine.causal_candidates(target, raw)
                for order, row in enumerate(rows):
                    row["venue"] = target.exchange_name
                    row["source_symbol"] = target.symbol
                    trade_symbol = trade_map.get(str(row["base"]).upper())
                    if not trade_symbol:
                        continue
                    if target.exchange_name != "Binance":
                        with trade_lock:
                            row = app.common.translate_to_binance(row, trade_symbol, trade)
                    key = f'{row["base"]}:{row["direction"]}:{row["portfolio_kind"]}'
                    if time.time() - app.common.f(state["signals"].get(key)) >= app.COOLDOWN:
                        candidates_by_index.append((index, order, row))
            except Exception as exc:
                if len(scan_errors) < 50:
                    scan_errors.append({
                        "venue": target.exchange_name,
                        "symbol": target.symbol,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                app.log.debug("scan %s failed: %s", target.symbol, exc)
        if processed % 10 == 0 or processed == expected:
            print(f"\r三大交易所加速掃描中  {processed}/{expected}", end="", flush=True)

    for thread in producers:
        thread.join(timeout=1)
    print(flush=True)
    candidates_by_index.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates_by_index], scan_errors


def main():
    global app
    if app.SHADOW and os.getenv("STATE_FILE") is None:
        app.STATE = app.ROOT / "radar_v009_shadow_state.json"
    lock = (app.ROOT / ("radar_v009_shadow.lock" if app.SHADOW else "radar_v004.lock")).open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("CryptoRadar V009 已在執行")

    trade = app.common.client()
    scanners = app.common.scan_clients(trade)
    trade_map = app.common.binance_trade_map(trade)
    trader = app.LiveTrader(trade, app.ROOT)
    state = app.load_state()
    stop = False
    pool = []
    account = trader.account() if app.SHADOW else trader.guard()
    alpaca_gate = alpaca_volume_gate_v009.AlpacaVolumeGate(app.ROOT)

    def halt(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, halt)
    signal.signal(signal.SIGTERM, halt)
    snap = trader.snapshot()
    # 正式升級後第一次啟動通知一次；同版本自動重啟不再通知。
    if not app.SHADOW and _NOTIFY_STARTUP and _startup_notice_due():
        app.common.tg(
            "📡 CryptoRadar V009 正式升級完成\n"
            "掃描：Binance、Bitget、BingX；三交易所安全平行抓取，相同標的合併後只下 Binance。\n"
            "策略：V157完整核心；原核心優先；總持倉最多5倉（其中新增模組最多2倉）；TP1最低4R。\n"
            "風控：全倉20倍；單筆停損風險1%；不設每日停開倉。\n"
            "出場：TP1分批、成本保護、TP2、TP3、5／10日線尾單。\n"
            f"目前交易所持倉：{snap['count']} 倉。"
        )
        _remember_startup_notice()
    elif not app.SHADOW and _NOTIFY_STARTUP:
        app.log.info("V009 同版本已通知過，已抑制重複 Telegram 啟動通知")
    elif app.SHADOW:
        app.log.info("V009 SHADOW mode; no orders and no Telegram")
    else:
        app.log.info("V009 正式實盤模式；Telegram 啟動通知已關閉")

    while not stop:
        started = time.time()
        try:
            if not pool:
                targets, errors = app.engine.discover_targets(scanners)
                pool = targets
                for target in targets:
                    if target.exchange_name == "Binance":
                        trade_map.setdefault(str(target.base).upper(), target.symbol)
                counts = {name: sum(x.exchange_name == name for x in targets) for name, _ in scanners}
                app.log.info("V009 merged market pool %d counts=%s discovery_errors=%d", len(pool), counts, len(errors))
                if app.SHADOW and app.RUN_ONCE and app.SHADOW_LIMIT > 0:
                    pool = pool[:app.SHADOW_LIMIT]
                    app.log.info("V009 shadow validation sample=%d; live mode remains full market pool", len(pool))

            if not app.SHADOW:
                trader.reconcile(state, app.common.tg, app.zhside, app.save_state)
            snap = trader.snapshot()
            active = app._active_for_policy(state, snap)
            active_bases = {str(x.get("base", "")).upper() for x in active}
            candidates, scan_errors = _scan_parallel(
                pool, active_bases, trade, trade_map, state, lambda: stop
            )
            stock_gate_rejected = []
            stock_gate_passed = []
            stock_gate_neutral = []
            gated_candidates = []
            for row in candidates:
                gate_result = alpaca_gate.evaluate(row)
                if not gate_result.is_stock_token:
                    gated_candidates.append(row)
                    continue
                audit_row = {
                    "venue": row.get("venue"),
                    "source_symbol": row.get("source_symbol"),
                    "base": row.get("base"),
                    **gate_result.as_dict(),
                }
                allowed, priority_bonus, decision_text = alpaca_volume_gate_v009.execution_decision(gate_result)
                row.update({
                    "美股代幣": True,
                    "Alpaca原股": gate_result.underlying,
                    "Alpaca最新量能倍數": gate_result.latest_volume_ratio,
                    "Alpaca近3根量能倍數": gate_result.volume_build_3,
                })
                if gate_result.passed:
                    row["priority"] = app.common.f(row.get("priority"), app.common.f(row.get("score"))) + priority_bonus
                    row["Alpaca量能確認"] = decision_text
                    gated_candidates.append(row)
                    stock_gate_passed.append(audit_row)
                    app.log.info(
                        "V009 Alpaca量能通過 %s 原股=%s 最新=%.2f 近3根=%.2f 排序+2",
                        row.get("base"), gate_result.underlying,
                        gate_result.latest_volume_ratio, gate_result.volume_build_3,
                    )
                elif allowed:
                    row["Alpaca量能確認"] = decision_text
                    gated_candidates.append(row)
                    stock_gate_neutral.append(audit_row)
                    app.log.info(
                        "V009 美股代幣中性放行 %s 原股=%s 原因=%s",
                        row.get("base"), gate_result.underlying, gate_result.reason,
                    )
                else:
                    stock_gate_rejected.append(audit_row)
                    app.log.info(
                        "V009 美股代幣資料無法驗證未進場 %s 原股=%s 原因=%s",
                        row.get("base"), gate_result.underlying, gate_result.reason,
                    )
            candidates = gated_candidates
            selected, rejected, _ = app.policy.select(candidates, active, state.get("history", []), time.time())

            if app.SHADOW:
                audit = {
                    "time": pd.Timestamp.now(tz="UTC").isoformat(),
                    "markets": len(pool), "raw_candidates": candidates,
                    "selected": selected, "rejected": rejected,
                    "scan_errors": scan_errors, "account": account,
                    "alpaca_volume_passed": stock_gate_passed,
                    "alpaca_volume_neutral_allowed": stock_gate_neutral,
                    "alpaca_volume_rejected": stock_gate_rejected,
                }
                (app.ROOT / "v009_shadow_result.json").write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
                app.log.info("V009 shadow complete markets=%d candidates=%d selected=%d", len(pool), len(candidates), len(selected))
                if app.RUN_ONCE:
                    break
                selected = []

            for row in selected:
                row["funding"] = app.common.funding(trade, row["symbol"])
                row["status"] = "pending"
                row["open_time"] = time.time()
                row["slot"] = "原核心優先倉" if row["portfolio_kind"] == "CORE" else "新增模組倉"
                key = f'{row["base"]}:{row["direction"]}:{row["portfolio_kind"]}'
                state["pending"] = {"key": key, "symbol": row["symbol"], "time": time.time()}
                app.save_state(state)
                try:
                    row.update(trader.enter(row, state))
                    row["status"] = "open"
                    state["signals"][key] = time.time()
                    state["positions"][key] = row
                    state.pop("pending", None)
                    app.save_state(state)
                    app.common.tg(app.signal_message(row))
                    app.log.info("V009 live accepted %s %s %s", row["base"], row["portfolio_kind"], row["direction"])
                except Exception as exc:
                    state.pop("pending", None)
                    app.save_state(state)
                    app.common.tg(f'⚠️ V009 未進場\n標的：{row["base"]}USDT\n原因：{app.chinese_reason(exc)}')
                    app.log.warning("V009 live rejected %s: %s", row["base"], exc)

            elapsed = time.time() - started
            app.log.info(
                "V009 本輪掃描完成 markets=%d %s elapsed=%.1fs",
                len(pool), f"selected={len(selected)}" if selected else "無新訊號", elapsed,
            )
            _touch_health(cycle_complete=True)
            _release_unused_memory()
        except Exception:
            app.log.exception("V009 cycle failed")

        remain = max(0, app.SCAN - (time.time() - started))
        while remain > 0 and not stop:
            wait = min(1, remain)
            time.sleep(wait)
            remain -= wait
    return 0


app.main = main


if __name__ == "__main__":
    raise SystemExit(main())
