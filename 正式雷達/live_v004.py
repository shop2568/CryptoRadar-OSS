#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V004 Binance USD-M 實盤層：完整風險、最低 4R、分批停利與動態均線尾單。"""
from __future__ import annotations

import time
import math
from datetime import datetime, timezone

import pandas as pd

from live_v003 import LiveTrader as V003Trader, LEVERAGE, RISK_PER_TRADE, CHASE_R, num


def daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame[["open", "high", "low", "close", "volume"]].copy()
    for column in data.columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close"])
    day = data.resample("1D").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna(subset=["close"])
    day["sma5"] = day["close"].rolling(5, min_periods=5).mean()
    day["sma10"] = day["close"].rolling(10, min_periods=10).mean()
    return day


def dynamic_trend_ma(history: pd.DataFrame, direction: str):
    if len(history) < 12:
        return 5, "日K不足，先用5日線保護"
    sample = history.tail(12); last = sample.iloc[-1]
    sign = 1.0 if direction == "LONG" else -1.0
    close, ma5, ma10 = float(last["close"]), float(last["sma5"]), float(last["sma10"])
    if not all(math.isfinite(value) for value in (close, ma5, ma10)):
        return 5, "均線資料不足，先用5日線保護"
    ma5_old, ma10_old = float(sample["sma5"].iloc[-4]), float(sample["sma10"].iloc[-4])
    ordered = sign * (close - ma5) > 0 and sign * (ma5 - ma10) > 0
    both_sloping = sign * (ma5 - ma5_old) > 0 and sign * (ma10 - ma10_old) > 0
    signed_move = sign * (close - float(sample["close"].iloc[-6]))
    travel = float(sample["close"].tail(6).diff().abs().sum())
    efficiency = signed_move / max(travel, 1e-12)
    extension = sign * (close - ma10) / max(abs(ma10), 1e-12)
    if ordered and both_sloping and efficiency >= 0.35 and extension <= 0.18:
        return 10, f"趨勢完整且順暢（效率{efficiency:.2f}），使用10日線"
    return 5, f"短線轉弱或趨勢不夠順（效率{efficiency:.2f}），使用5日線"


class LiveTrader(V003Trader):
    def daily_allowed(self, state):
        """V101 回測沒有每日 -2% 停開倉；保留紀錄但不封鎖訊號。"""
        account = self.account()
        day = datetime.now(timezone.utc).date().isoformat()
        data = state.setdefault("daily", {})
        if data.get("date") != day or num(data.get("start_equity")) <= 0:
            data.clear(); data.update(date=day, start_equity=account["equity"], locked=False)
        start = num(data.get("start_equity"))
        loss = (start - account["equity"]) / start if start > 0 else 0.0
        return True, loss, account

    def _minimum_live_rr(self, _row):
        return 4.0

    def _size(self, row, price, state):
        """沿用原風控計算，但讓 V004 的拒單原因完整顯示中文與數字。"""
        planned = num(row.get("entry"))
        stop = num(row.get("stop"))
        tp1 = num(row.get("tp1"))
        side = str(row.get("direction") or "").upper()
        allowed = (
            planned + CHASE_R * abs(planned - stop)
            if side == "LONG"
            else planned - CHASE_R * abs(planned - stop)
        )
        try:
            return super()._size(row, price, state)
        except RuntimeError as exc:
            reason = str(exc)
            if reason == "price chased above allowed range":
                raise RuntimeError(
                    f"目前價格已高於可接受進場範圍，不追高；"
                    f"原定進場 {planned:.10g}，最高接受 {allowed:.10g}，目前 {price:.10g}"
                ) from exc
            if reason == "price chased below allowed range":
                raise RuntimeError(
                    f"目前價格已低於可接受進場範圍，不追空；"
                    f"原定進場 {planned:.10g}，最低接受 {allowed:.10g}，目前 {price:.10g}"
                ) from exc
            if reason == "invalid live risk":
                raise RuntimeError(
                    f"即時價格與停損位置不合理，無法計算安全倉位；目前 {price:.10g}，停損 {stop:.10g}"
                ) from exc
            if reason.startswith("live RR below"):
                risk = price - stop if side == "LONG" else stop - price
                rr = ((tp1 - price) / risk if side == "LONG" else (price - tp1) / risk) if risk > 0 else 0.0
                raise RuntimeError(f"目前價格進場後，TP1 報酬比只剩 {rr:.2f}R，低於最低 4R") from exc
            if reason == "position below exchange minimum":
                raise RuntimeError("依照單筆風險計算出的下單金額低於幣安最低下單限制") from exc
            if reason.startswith("daily loss lock"):
                raise RuntimeError("當日虧損保護已啟動，暫停新增部位") from exc
            raise

    def _client_id(self, prefix):
        return ("V004" + prefix + str(int(time.time() * 1000)))[-32:]

    def _algo_qty(self, sid, side, kind, trigger, quantity, client_id):
        return self.ex.fapiPrivatePostAlgoOrder({
            "algoType": "CONDITIONAL", "symbol": sid, "side": side,
            "positionSide": "BOTH", "type": kind, "triggerPrice": trigger,
            "quantity": quantity, "reduceOnly": "TRUE", "workingType": "MARK_PRICE",
            "priceProtect": "FALSE", "clientAlgoId": client_id, "newOrderRespType": "ACK",
        })

    def _weights(self, base):
        return (0.25, 0.20, 0.15, 0.40) if str(base).upper() in {"BTC", "ETH"} else (0.50, 0.20, 0.15, 0.15)

    def _quantity(self, symbol, quantity):
        return num(self.ex.amount_to_precision(symbol, max(0.0, quantity)))

    def enter(self, row, state):
        flag = self.root / "LIVE_TRADING_ENABLED"
        if not flag.exists() or flag.read_text(encoding="utf-8").strip() != "I_ACCEPT_LIVE_TRADING":
            raise RuntimeError("實盤開關尚未開啟")
        snap = self.snapshot()
        if snap["count"] >= 5:
            raise RuntimeError("目前已滿五倉")
        if row["symbol"] in snap["symbols"] or row["base"] in snap["bases"]:
            raise RuntimeError("同標的已有持倉")

        price = self._live_price(row)
        qty, _, account, day_loss = self._size(row, price, state)
        _, sid = self._set_risk_mode(row["symbol"])
        side = "BUY" if row["direction"] == "LONG" else "SELL"
        exit_side = "SELL" if row["direction"] == "LONG" else "BUY"
        order = self.ex.fapiPrivatePostOrder({
            "symbol": sid, "side": side, "type": "MARKET",
            "quantity": self.ex.amount_to_precision(row["symbol"], qty),
            "newClientOrderId": self._client_id("ENT"), "newOrderRespType": "RESULT",
        })
        executed = num(order.get("executedQty")) or qty
        avg = num(order.get("avgPrice")) or price
        time.sleep(0.8)
        position = self._position(row["symbol"])
        signed = executed if row["direction"] == "LONG" else -executed
        if position:
            avg = num(position.get("entryPrice")) or avg
            signed = num((position.get("info") or {}).get("positionAmt"), signed)
        risk = avg - num(row["stop"]) if row["direction"] == "LONG" else num(row["stop"]) - avg
        actual_rr = ((num(row["tp1"]) - avg) / risk if row["direction"] == "LONG" else (avg - num(row["tp1"])) / risk) if risk > 0 else 0.0
        if actual_rr < 4.0:
            self._close_market(row["symbol"], signed, f"實際成交後只剩 {actual_rr:.2f}R")
            raise RuntimeError(f"成交後 TP1 不足 4R（{actual_rr:.2f}R），已立即平倉")

        tp1_w, tp2_w, tp3_w, runner_w = self._weights(row["base"])
        tp1_qty = self._quantity(row["symbol"], executed * tp1_w)
        if tp1_qty <= 0:
            self._close_market(row["symbol"], signed, "TP1 數量低於交易所最小值")
            raise RuntimeError("TP1 數量低於交易所最小值，已立即平倉")
        stop_order = tp1_order = None
        try:
            stop_order = self._algo(sid, exit_side, "STOP_MARKET", self.ex.price_to_precision(row["symbol"], row["stop"]), self._client_id("SL"))
            tp1_order = self._algo_qty(sid, exit_side, "TAKE_PROFIT_MARKET", self.ex.price_to_precision(row["symbol"], row["tp1"]), self.ex.amount_to_precision(row["symbol"], tp1_qty), self._client_id("TP1"))
        except Exception as exc:
            if stop_order: self._cancel_algo(sid, stop_order.get("algoId"))
            if tp1_order: self._cancel_algo(sid, tp1_order.get("algoId"))
            self._close_market(row["symbol"], signed, f"保護單建立失敗：{exc}")
            raise RuntimeError("保護單建立失敗，已立即平倉") from exc
        return {
            "live": True, "entry_order_id": order.get("orderId"), "quantity": executed,
            "initial_quantity": executed, "actual_entry": avg, "actual_rr": actual_rr,
            "leverage": LEVERAGE, "risk_usdt": account["equity"] * RISK_PER_TRADE,
            "stop_algo_id": stop_order.get("algoId"), "tp1_algo_id": tp1_order.get("algoId"),
            "tp2_algo_id": None, "tp3_algo_id": None, "exchange_symbol": sid,
            "tp_stage": 0, "tp1_weight": tp1_w, "tp2_weight": tp2_w,
            "tp3_weight": tp3_w, "runner_weight": runner_w, "day_loss_at_entry": day_loss,
        }

    def _finished(self, order):
        return str(order.get("algoStatus") or order.get("status") or "").upper() in {"FINISHED", "FILLED", "TRIGGERED"}

    def _replace_stop_at_entry(self, row, sid, exit_side):
        new_stop = self._algo(sid, exit_side, "STOP_MARKET", self.ex.price_to_precision(row["symbol"], row["actual_entry"]), self._client_id("BE"))
        self._cancel_algo(sid, row.get("stop_algo_id"))
        row["stop_algo_id"] = new_stop.get("algoId")

    def _place_later_targets(self, row, sid, exit_side):
        initial = num(row.get("initial_quantity"), num(row.get("quantity")))
        for stage, key, weight in ((2, "tp2", num(row.get("tp2_weight"))), (3, "tp3", num(row.get("tp3_weight")))):
            quantity = self._quantity(row["symbol"], initial * weight)
            if quantity <= 0: continue
            order = self._algo_qty(sid, exit_side, "TAKE_PROFIT_MARKET", self.ex.price_to_precision(row["symbol"], row[key]), self.ex.amount_to_precision(row["symbol"], quantity), self._client_id(f"TP{stage}"))
            row[f"tp{stage}_algo_id"] = order.get("algoId")

    def _ma_exit(self, row):
        since = int((time.time() - 45 * 86400) * 1000)
        candles = self.ex.fetch_ohlcv(row["symbol"], "1d", since=since, limit=50)
        if len(candles) < 12: return None
        frame = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
        frame["time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
        frame = frame.set_index("time").drop(columns="ts")
        # 排除今天尚未收完的日 K。
        frame = frame[frame.index < pd.Timestamp.now(tz="UTC").floor("1D")]
        day = daily_frame(frame)
        if len(day) < 12: return None
        period, reason = dynamic_trend_ma(day, row["direction"])
        last = day.iloc[-1]
        close = num(last["close"]); ma = num(last[f"sma{period}"])
        crossed = close < ma if row["direction"] == "LONG" else close > ma
        return period, reason, close, ma, crossed, day.index[-1].isoformat()

    def force_close(self, row, state, reason="核心訊號優先讓位"):
        position = self._position(row["symbol"])
        sid = row.get("exchange_symbol") or self.ex.market(row["symbol"])["id"]
        for key in ("stop_algo_id", "tp1_algo_id", "tp2_algo_id", "tp3_algo_id"):
            self._cancel_algo(sid, row.get(key))
        if position:
            signed = num((position.get("info") or {}).get("positionAmt"))
            self._close_market(row["symbol"], signed, reason)
        row.update(status="closed", exit_reason=reason, exit_time=time.time())
        state.setdefault("history", []).append(dict(row))

    def reconcile(self, state, tg, zhside, save_state):
        changed = False
        for key, row in list(state.get("positions", {}).items()):
            if not row.get("live"): continue
            try:
                position = self._position(row["symbol"])
                sid = row.get("exchange_symbol") or self.ex.market(row["symbol"])["id"]
                exit_side = "SELL" if row["direction"] == "LONG" else "BUY"
                stop_order = self._algo_status(sid, row.get("stop_algo_id"))
                tp1_order = self._algo_status(sid, row.get("tp1_algo_id"))
                if not position:
                    for name in ("stop_algo_id", "tp1_algo_id", "tp2_algo_id", "tp3_algo_id"):
                        self._cancel_algo(sid, row.get(name))
                    reason = "TP1後結束" if int(row.get("tp_stage", 0)) >= 1 else ("TP1" if self._finished(tp1_order) else "停損")
                    row.update(status="closed", exit_reason=reason, exit_time=time.time())
                    state.setdefault("history", []).append(dict(row)); del state["positions"][key]
                    tg(f'📌 V004｜實盤交易結束\n標的：{row["base"]}USDT\n方向：{zhside(row["direction"])}\n結果：{reason}')
                    changed = True; continue
                if not self._active_algo(stop_order):
                    signed = num((position.get("info") or {}).get("positionAmt"))
                    for name in ("tp1_algo_id", "tp2_algo_id", "tp3_algo_id"):
                        self._cancel_algo(sid, row.get(name))
                    self._close_market(row["symbol"], signed, "停損保護失效")
                    tg(f'⛔ V004 緊急平倉\n標的：{row["base"]}USDT\n原因：交易所停損保護失效')
                    continue
                if int(row.get("tp_stage", 0)) == 0 and self._finished(tp1_order):
                    self._replace_stop_at_entry(row, sid, exit_side)
                    self._place_later_targets(row, sid, exit_side)
                    row["tp_stage"] = 1; row["tp1_time"] = time.time(); changed = True
                    tg(f'✅ V004｜{row["base"]}USDT 到達 TP1\n已賣出 {num(row["tp1_weight"])*100:.0f}%\n剩餘部位停損已移到成本，繼續看 TP2、TP3 與 5／10 日線。')
                if int(row.get("tp_stage", 0)) >= 1:
                    for stage in (2, 3):
                        order = self._algo_status(sid, row.get(f"tp{stage}_algo_id"))
                        if self._finished(order) and not row.get(f"tp{stage}_notified"):
                            row[f"tp{stage}_notified"] = True; changed = True
                            tg(f'✅ V004｜{row["base"]}USDT 到達 TP{stage}')
                    check = self._ma_exit(row)
                    if check and check[-1] != row.get("ma_checked_day"):
                        period, reason, close, ma, crossed, day = check
                        row["ma_checked_day"] = day; row["active_ma"] = period; changed = True
                        if crossed:
                            for name in ("stop_algo_id", "tp2_algo_id", "tp3_algo_id"):
                                self._cancel_algo(sid, row.get(name))
                            signed = num((position.get("info") or {}).get("positionAmt"))
                            self._close_market(row["symbol"], signed, f"{period}日均線退出")
                            tg(f'📉 V004｜{row["base"]}USDT 尾單退出\n原因：收盤跌破／站上 {period} 日線\n判斷：{reason}')
            except Exception:
                import logging; logging.getLogger("V004").exception("reconcile %s failed", key)
        if changed: save_state(state)
        return changed
