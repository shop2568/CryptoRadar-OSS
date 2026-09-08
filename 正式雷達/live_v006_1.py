#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.1 實盤層。

V006.1 修訂重點（V006 的小版本，不升主版本）：
- 全倉固定 20 倍；20x 只影響保證金，不放大單筆停損風險。
- 單筆停損風險 1%，單倉名目價值上限為權益 1.5 倍。
- TP1 前：原始結構停損固定不移動。
- TP1：平原始部位 50%，其後才把剩餘部位移到「含回測成本模型」的保本停損。
- TP2：平原始部位 25%（只有 TP2 是獨立、有效目標時才掛）。
- TP3：平所有剩餘部位；若沒有獨立 TP2，TP3 可直接承接 TP1 後全部剩餘部位。
- TP1 後：固定選定 SMA5 或 SMA10；以完成的 15m 收盤確認弱化，下一次輪詢用市價退出剩餘部位。
- TP1 後任何保護單／分批單建立失敗，Telegram 立即告警；成本保護停損優先保留。

注意：V139_A 本身是進場前硬閘門，不在這個 LiveTrader 檔案內實作。
"""

from __future__ import annotations

import logging
import math
import time

import pandas as pd

from live_v003 import CHASE_R, num
from live_v004 import daily_frame, dynamic_trend_ma
from live_v005 import LiveTrader as V005Trader


LEVERAGE = 20
RISK_PER_TRADE = 0.01
PER_POSITION_NOTIONAL = 1.50

# 回測長期使用的單邊成本模型：fee 0.05% + slippage 0.04% = 0.09% / side。
# TP1 後的「含成本保本」使用相同模型，避免只掛在裸 entry 而仍被雙邊成本吃成小虧。
BACKTEST_COST_PER_SIDE = 0.0009


class LiveTrader(V005Trader):
    """V006.1：固定 20x + 1% 結構停損風險 + V139 系列分批出場。"""

    def _client_id(self, prefix):
        return ("V0061" + prefix + str(int(time.time() * 1000)))[-32:]

    def _set_risk_mode(self, symbol):
        market, exchange_symbol = self.market_info(symbol)
        try:
            self.ex.fapiPrivatePostMarginType({
                "symbol": exchange_symbol,
                "marginType": "CROSSED",
            })
        except Exception as exc:
            # Binance -4046 = already in requested margin mode.
            if "-4046" not in str(exc):
                raise
        self.ex.fapiPrivatePostLeverage({
            "symbol": exchange_symbol,
            "leverage": LEVERAGE,
        })
        return market, exchange_symbol

    def _size(self, row, price, state):
        allowed, day_loss, account = self.daily_allowed(state)
        if not allowed:
            raise RuntimeError(f"當日虧損保護已啟動（目前 {day_loss:.2%}），暫停新增部位")

        planned = num(row.get("entry"))
        stop = num(row.get("stop"))
        tp1 = num(row.get("tp1"))
        direction = str(row.get("direction") or "").upper()
        risk_per_unit = price - stop if direction == "LONG" else stop - price
        if min(price, risk_per_unit) <= 0:
            raise RuntimeError(
                f"即時價格與停損位置不合理，無法計算安全倉位；目前 {price:.10g}，停損 {stop:.10g}"
            )

        if direction == "LONG":
            chase_limit = planned + CHASE_R * abs(planned - stop)
            if price > chase_limit:
                raise RuntimeError(
                    f"目前價格已高於可接受進場範圍，不追高；原定進場 {planned:.10g}，"
                    f"最高接受 {chase_limit:.10g}，目前 {price:.10g}"
                )
            rr = (tp1 - price) / risk_per_unit
        else:
            chase_limit = planned - CHASE_R * abs(planned - stop)
            if price < chase_limit:
                raise RuntimeError(
                    f"目前價格已低於可接受進場範圍，不追空；原定進場 {planned:.10g}，"
                    f"最低接受 {chase_limit:.10g}，目前 {price:.10g}"
                )
            rr = (price - tp1) / risk_per_unit

        if rr < 4.0:
            raise RuntimeError(f"目前價格進場後，TP1 報酬比只剩 {rr:.2f}R，低於最低 4R")

        # 結構停損真正打到時，理論損失預算 = 當下權益 1%。
        risk_quantity = account["equity"] * RISK_PER_TRADE / risk_per_unit

        # 20x 只降低交易所占用保證金；不把曝險直接放大到 20x equity。
        maximum_notional = min(
            account["equity"] * PER_POSITION_NOTIONAL,
            account["available"] * LEVERAGE * 0.90,
        )
        quantity = min(risk_quantity, maximum_notional / price)
        quantity = num(self.ex.amount_to_precision(row["symbol"], quantity))

        market = self.ex.market(row["symbol"])
        limits = market.get("limits", {})
        minimum_amount = num((limits.get("amount") or {}).get("min"))
        minimum_cost = max(5.0, num((limits.get("cost") or {}).get("min")))
        if quantity <= 0 or quantity < minimum_amount or quantity * price < minimum_cost:
            raise RuntimeError("依照單筆風險計算出的下單金額低於幣安最低下單限制")
        return quantity, rr, account, day_loss

    # ------------------------------------------------------------------
    # V139 系列分批：所有標的一致使用 50 / 25 / 25。
    # ------------------------------------------------------------------
    def _weights(self, _base):
        return 0.50, 0.25, 0.25, 0.0

    def _quantity(self, symbol, quantity):
        return num(self.ex.amount_to_precision(symbol, max(0.0, quantity)))

    def _position_quantity(self, row):
        position = self._position(row["symbol"])
        if not position:
            return 0.0
        info = position.get("info") or {}
        return abs(num(info.get("positionAmt")))

    def _cost_break_even_price(self, row):
        """依回測雙邊成本模型計算 TP1 後剩餘部位的含成本保本價。"""
        entry = num(row.get("actual_entry"), num(row.get("entry")))
        if entry <= 0:
            raise RuntimeError("找不到有效實際進場價，無法建立含成本保本")
        c = BACKTEST_COST_PER_SIDE
        if str(row.get("direction") or "").upper() == "LONG":
            # stop-entry = c*(entry+stop)
            return entry * (1.0 + c) / (1.0 - c)
        # entry-stop = c*(entry+stop)
        return entry * (1.0 - c) / (1.0 + c)

    def _replace_stop_at_entry(self, row, sid, exit_side):
        """V004 會在 TP1 後呼叫此方法；V006 改成含成本保本，而不是裸 entry。"""
        protection = self._cost_break_even_price(row)
        trigger = self.ex.price_to_precision(row["symbol"], protection)

        # 先把新保護掛成功，再取消舊結構停損，避免保護空窗。
        new_stop = self._algo(
            sid,
            exit_side,
            "STOP_MARKET",
            trigger,
            self._client_id("COSTBE"),
        )
        old_stop = row.get("stop_algo_id")
        row["stop_algo_id"] = new_stop.get("algoId")
        row["post_tp1_protection_price"] = num(trigger, protection)
        row["post_tp1_protection_model"] = "BACKTEST_COST_BE"
        if old_stop:
            try:
                self._cancel_algo(sid, old_stop)
            except Exception:
                # 新保護已存在；舊單取消失敗不應把新保護一起撤掉。
                logging.getLogger("V006").exception("cancel old stop failed: %s", old_stop)
        return new_stop

    @staticmethod
    def _target_is_further(direction, first, later):
        first = num(first)
        later = num(later)
        if first <= 0 or later <= 0:
            return False
        return later > first if str(direction).upper() == "LONG" else later < first

    def _existing_algo_ok(self, sid, algo_id):
        if not algo_id:
            return False
        try:
            order = self._algo_status(sid, algo_id)
            return self._active_algo(order) or self._finished(order)
        except Exception:
            return False

    def _probe_algo(self, sid, algo_id):
        """查詢條件單，但把「查詢失敗」與「確定失效」分開。

        舊版 `_algo_status` 會把任何 API 例外都變成空字典，導致一次短暫查詢
        失敗被誤判成停損消失，接著立刻市價平倉。V006.1 不再這樣做。
        """
        if not algo_id:
            return {}, "MISSING", None
        try:
            order = self.ex.fapiPrivateGetAlgoOrder({"symbol": sid, "algoId": algo_id})
            status = str(order.get("algoStatus") or order.get("status") or "").upper()
            return order, status or "UNKNOWN", None
        except Exception as exc:
            return {}, "QUERY_ERROR", exc

    @staticmethod
    def _retry_due(row, key, seconds=10.0):
        now = time.time()
        last = num(row.get(key))
        if now - last < seconds:
            return False
        row[key] = now
        return True

    def _create_original_stop(self, row, sid, exit_side):
        trigger = self.ex.price_to_precision(row["symbol"], num(row["stop"]))
        order = self._algo(sid, exit_side, "STOP_MARKET", trigger, self._client_id("SL"))
        row["stop_algo_id"] = order.get("algoId")
        row["stop_order_retry"] = False
        row.pop("stop_order_error", None)
        return order

    def _create_tp1(self, row, sid, exit_side):
        initial = num(row.get("initial_quantity"), num(row.get("quantity")))
        quantity = self._quantity(row["symbol"], initial * 0.50)
        if quantity <= 0:
            raise RuntimeError("TP1 數量低於交易所最小值")
        order = self._algo_qty(
            sid,
            exit_side,
            "TAKE_PROFIT_MARKET",
            self.ex.price_to_precision(row["symbol"], num(row["tp1"])),
            self.ex.amount_to_precision(row["symbol"], quantity),
            self._client_id("TP1"),
        )
        row["tp1_algo_id"] = order.get("algoId")
        row["tp1_order_retry"] = False
        row.pop("tp1_order_error", None)
        return order

    def _select_post_tp1_ma(self, row):
        """在 TP1 首次確認時固定選 SMA5 或 SMA10，只使用已完成 UTC 日 K。"""
        if int(num(row.get("post_tp1_ma_days"))) in (5, 10):
            return int(row["post_tp1_ma_days"]), str(row.get("post_tp1_ma_reason") or "已固定")

        since = int((time.time() - 60 * 86400) * 1000)
        candles = self.ex.fetch_ohlcv(row["symbol"], "1d", since=since, limit=70)
        if len(candles) < 12:
            raise RuntimeError("TP1 後日線歷史不足，無法選擇 5/10 日線")

        frame = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
        frame["time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
        frame = frame.set_index("time").drop(columns="ts")
        frame = frame[frame.index < pd.Timestamp.now(tz="UTC").floor("1D")]
        day = daily_frame(frame)
        if len(day) < 12:
            raise RuntimeError("TP1 後已完成日 K 不足，無法選擇 5/10 日線")

        period, reason = dynamic_trend_ma(day, row["direction"])
        row["post_tp1_ma_days"] = int(period)
        row["post_tp1_ma_reason"] = str(reason)
        row["post_tp1_ma_selected_at"] = time.time()
        return int(period), str(reason)

    def _place_later_targets(self, row, sid, exit_side):
        """TP1 後依實際剩餘倉位建立 TP2/TP3，並與回測的 50/25/剩餘語意一致。"""
        self._select_post_tp1_ma(row)

        initial = num(row.get("initial_quantity"), num(row.get("quantity")))
        remaining = self._position_quantity(row)
        if initial <= 0 or remaining <= 0:
            return

        direction = str(row.get("direction") or "").upper()
        tp1 = num(row.get("tp1"))
        tp2 = num(row.get("tp2"))
        tp3 = num(row.get("tp3"))

        distinct2 = self._target_is_further(direction, tp1, tp2)
        base_for_tp3 = tp2 if distinct2 else tp1
        distinct3 = self._target_is_further(direction, base_for_tp3, tp3)

        tp2_qty = 0.0
        if distinct2:
            tp2_qty = min(self._quantity(row["symbol"], initial * 0.25), remaining)
            if tp2_qty > 0 and not self._existing_algo_ok(sid, row.get("tp2_algo_id")):
                order = self._algo_qty(
                    sid,
                    exit_side,
                    "TAKE_PROFIT_MARKET",
                    self.ex.price_to_precision(row["symbol"], tp2),
                    self.ex.amount_to_precision(row["symbol"], tp2_qty),
                    self._client_id("TP2"),
                )
                row["tp2_algo_id"] = order.get("algoId")
                row["tp2_planned_qty"] = tp2_qty
        else:
            row["tp2_skipped_reason"] = "TP2不是TP1之後的獨立有利目標"

        if distinct3:
            # 有有效 TP2：TP3 吃掉扣除 TP2 預留後的全部剩餘。
            # 無有效 TP2：TP3 可直接吃掉 TP1 後全部剩餘，對齊回測語意。
            reserved_tp2 = tp2_qty if distinct2 else 0.0
            tp3_qty = self._quantity(row["symbol"], max(0.0, remaining - reserved_tp2))
            if tp3_qty > 0 and not self._existing_algo_ok(sid, row.get("tp3_algo_id")):
                order = self._algo_qty(
                    sid,
                    exit_side,
                    "TAKE_PROFIT_MARKET",
                    self.ex.price_to_precision(row["symbol"], tp3),
                    self.ex.amount_to_precision(row["symbol"], tp3_qty),
                    self._client_id("TP3"),
                )
                row["tp3_algo_id"] = order.get("algoId")
                row["tp3_planned_qty"] = tp3_qty
        else:
            row["tp3_skipped_reason"] = "TP3不是前一有效目標之後的獨立有利目標"

    def _completed_daily_ma_line(self, row, period):
        since = int((time.time() - 60 * 86400) * 1000)
        candles = self.ex.fetch_ohlcv(row["symbol"], "1d", since=since, limit=70)
        if len(candles) < max(12, period + 2):
            return None
        frame = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
        frame["time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
        frame = frame.set_index("time").drop(columns="ts")
        frame = frame[frame.index < pd.Timestamp.now(tz="UTC").floor("1D")]
        day = daily_frame(frame)
        if day.empty:
            return None
        value = num(day.iloc[-1].get(f"sma{period}"))
        return value if value > 0 and math.isfinite(value) else None

    def _ma_exit(self, row):
        """TP1 後用完成 15m 收盤確認 5/10D MA 弱化；輪詢成交近似下一根 15m 開盤。"""
        period, reason = self._select_post_tp1_ma(row)
        ma = self._completed_daily_ma_line(row, period)
        if not ma:
            return None

        candles = self.ex.fetch_ohlcv(row["symbol"], "15m", limit=8)
        if len(candles) < 3:
            return None
        frame = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
        frame["time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
        frame = frame.set_index("time").drop(columns="ts")

        current_15m = pd.Timestamp.now(tz="UTC").floor("15min")
        closed = frame[frame.index < current_15m]
        if closed.empty:
            return None
        last = closed.iloc[-1]
        close = num(last["close"])
        crossed = close < ma if row["direction"] == "LONG" else close > ma
        return period, reason, close, ma, crossed, closed.index[-1].isoformat()

    def enter(self, row, state):
        # 不再呼叫 V004.enter：舊版在 SL 或 TP1 建立失敗時，會取消已建立的
        # 條件單並立刻用市價砍倉。STRC 的 3 秒出場正是這條路徑。
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
            "symbol": sid,
            "side": side,
            "type": "MARKET",
            "quantity": self.ex.amount_to_precision(row["symbol"], qty),
            "newClientOrderId": self._client_id("ENT"),
            "newOrderRespType": "RESULT",
        })
        executed = num(order.get("executedQty")) or qty
        avg = num(order.get("avgPrice")) or price
        time.sleep(0.8)
        exchange_position = self._position(row["symbol"])
        signed = executed if row["direction"] == "LONG" else -executed
        if exchange_position:
            avg = num(exchange_position.get("entryPrice")) or avg
            signed = num((exchange_position.get("info") or {}).get("positionAmt"), signed)

        risk = avg - num(row["stop"]) if row["direction"] == "LONG" else num(row["stop"]) - avg
        actual_rr = (
            (num(row["tp1"]) - avg) / risk
            if row["direction"] == "LONG"
            else (avg - num(row["tp1"])) / risk
        ) if risk > 0 else 0.0
        # 最低 4R 是策略硬條件，不屬於「保護單異常」。成交後已不合格仍照原規則退出。
        if actual_rr < 4.0:
            self._close_market(row["symbol"], signed, f"實際成交後只剩 {actual_rr:.2f}R")
            raise RuntimeError(f"成交後 TP1 不足 4R（{actual_rr:.2f}R），已立即平倉")

        position = {
            "live": True,
            "entry_order_id": order.get("orderId"),
            "quantity": executed,
            "initial_quantity": executed,
            "actual_entry": avg,
            "actual_rr": actual_rr,
            "leverage": LEVERAGE,
            "risk_usdt": account["equity"] * RISK_PER_TRADE,
            "stop_algo_id": None,
            "tp1_algo_id": None,
            "tp2_algo_id": None,
            "tp3_algo_id": None,
            "exchange_symbol": sid,
            "tp_stage": 0,
            "tp1_weight": 0.50,
            "tp2_weight": 0.25,
            "tp3_weight": 0.25,
            "runner_weight": 0.0,
            "day_loss_at_entry": day_loss,
        }

        # 停損優先。任何一張條件單失敗都不再自動砍倉；狀態交給 reconcile
        # 持續補掛，Telegram 會清楚告警。
        pending = {**row, **position}
        try:
            self._create_original_stop(pending, sid, exit_side)
            position["stop_algo_id"] = pending.get("stop_algo_id")
            position["stop_order_retry"] = False
        except Exception as exc:
            position["stop_order_retry"] = True
            position["stop_order_error"] = str(exc)

        try:
            pending.update(position)
            self._create_tp1(pending, sid, exit_side)
            position["tp1_algo_id"] = pending.get("tp1_algo_id")
            position["tp1_order_retry"] = False
        except Exception as exc:
            position["tp1_order_retry"] = True
            position["tp1_order_error"] = str(exc)

        position["leverage"] = LEVERAGE
        position["risk_usdt"] = num(position.get("risk_usdt"), 0.0)
        position["position_notional_cap"] = PER_POSITION_NOTIONAL
        position["margin_mode"] = "全倉"
        position["exit_policy"] = "V139_STYLE_TP1_50_TP2_25_TP3_REST_PLUS_MA"
        position["pre_tp1_stop_policy"] = "ORIGINAL_STRUCTURE_STOP_ONLY"
        position["post_tp1_protection"] = "COST_BREAK_EVEN"
        return position

    def reconcile(self, state, tg, zhside, save_state):
        """V006 自己管理 TP1 後狀態，避免 V004 例外只寫 log 而沒有 Telegram 告警。"""
        changed = False
        for key, row in list(state.get("positions", {}).items()):
            if not row.get("live"):
                continue
            try:
                position = self._position(row["symbol"])
                sid = row.get("exchange_symbol") or self.ex.market(row["symbol"])["id"]
                exit_side = "SELL" if row["direction"] == "LONG" else "BUY"
                stop_order, stop_status, stop_error = self._probe_algo(sid, row.get("stop_algo_id"))
                tp1_order, tp1_status, tp1_error = self._probe_algo(sid, row.get("tp1_algo_id"))

                if not position:
                    for name in ("stop_algo_id", "tp1_algo_id", "tp2_algo_id", "tp3_algo_id"):
                        self._cancel_algo(sid, row.get(name))
                    if int(row.get("tp_stage", 0)) >= 1:
                        reason = "TP1後依規則結束"
                    elif self._finished(tp1_order):
                        reason = "TP1"
                    elif self._finished(stop_order):
                        reason = "停損"
                    else:
                        reason = "外部或人工結束（非停損成交）"
                    row.update(status="closed", exit_reason=reason, exit_time=time.time())
                    state.setdefault("history", []).append(dict(row))
                    del state["positions"][key]
                    tg(
                        f'📌 V006｜實盤交易結束\n標的：{row["base"]}USDT\n'
                        f'方向：{zhside(row["direction"])}\n結果：{reason}'
                    )
                    changed = True
                    continue

                # 查詢失敗不等於停損消失；確定缺失／失效才補掛。無論哪一種情況，
                # 都不再取消 TP 或市價砍倉。
                if not self._active_algo(stop_order):
                    if stop_status == "QUERY_ERROR":
                        row["stop_order_retry"] = True
                        row["stop_order_error"] = f"停損查詢暫時失敗：{stop_error}"
                    elif self._retry_due(row, "last_stop_retry_at"):
                        try:
                            if int(row.get("tp_stage", 0)) >= 1:
                                self._replace_stop_at_entry(row, sid, exit_side)
                            else:
                                self._create_original_stop(row, sid, exit_side)
                            changed = True
                        except Exception as exc:
                            row["stop_order_retry"] = True
                            row["stop_order_error"] = str(exc)
                            changed = True

                    now = time.time()
                    if now - num(row.get("last_stop_warning_at")) >= 900:
                        row["last_stop_warning_at"] = now
                        changed = True
                        tg(
                            f'⚠️ V006.1｜{row["base"]}USDT 停損單目前無法確認\n'
                            '持倉不會被自動市價砍掉，系統會繼續補掛原停損。\n'
                            f'錯誤：{row.get("stop_order_error", stop_status)}'
                        )

                # TP1 缺失只補掛 TP1，原始結構停損和持倉都保留。
                if int(row.get("tp_stage", 0)) == 0 and not self._active_algo(tp1_order) and not self._finished(tp1_order):
                    if tp1_status == "QUERY_ERROR":
                        row["tp1_order_retry"] = True
                        row["tp1_order_error"] = f"TP1查詢暫時失敗：{tp1_error}"
                    elif self._retry_due(row, "last_tp1_retry_at"):
                        try:
                            self._create_tp1(row, sid, exit_side)
                            changed = True
                        except Exception as exc:
                            row["tp1_order_retry"] = True
                            row["tp1_order_error"] = str(exc)
                            changed = True

                # TP1 前完全不動原始結構停損。只有確認 TP1 已完成後才進入成本保護。
                if int(row.get("tp_stage", 0)) == 0 and self._finished(tp1_order):
                    try:
                        self._replace_stop_at_entry(row, sid, exit_side)
                        row["tp_stage"] = 1
                        row["tp1_time"] = time.time()
                        changed = True
                        # 成本保護最重要，先把它持久化；後續 TP2/TP3 就算 API 暫時失敗也能安全重試。
                        save_state(state)
                    except Exception as exc:
                        tg(
                            f'⛔ V006｜{row["base"]}USDT TP1 後成本保護建立失敗\n'
                            f'原結構停損仍保留，尚未切換 TP1 後階段。\n錯誤：{exc}'
                        )
                        raise

                    try:
                        self._place_later_targets(row, sid, exit_side)
                        changed = True
                        save_state(state)
                    except Exception as exc:
                        # 已有成本保護，不緊急平倉；保留部位並在下一輪重試缺失的 TP2/TP3。
                        row["later_targets_retry"] = True
                        row["later_targets_error"] = str(exc)
                        changed = True
                        save_state(state)
                        tg(
                            f'⚠️ V006｜{row["base"]}USDT TP1 已完成、成本保護已建立，'
                            f'但 TP2/TP3 建立未完整\n下一輪會重試。\n錯誤：{exc}'
                        )

                    tg(
                        f'✅ V006｜{row["base"]}USDT 到達 TP1\n'
                        '已賣出 50%\n'
                        f'剩餘部位已移到含成本保本 {num(row.get("post_tp1_protection_price")):.10g}\n'
                        f'後續：TP2 25%／TP3 全部剩餘／{int(num(row.get("post_tp1_ma_days"), 5))}日線弱化先出。'
                    )

                if int(row.get("tp_stage", 0)) >= 1:
                    # 若上一次 TP2/TP3 建立不完整，保持成本停損並安全重試。
                    if row.get("later_targets_retry"):
                        try:
                            self._place_later_targets(row, sid, exit_side)
                            row["later_targets_retry"] = False
                            row.pop("later_targets_error", None)
                            changed = True
                        except Exception as exc:
                            row["later_targets_error"] = str(exc)

                    for stage in (2, 3):
                        order = self._algo_status(sid, row.get(f"tp{stage}_algo_id"))
                        if self._finished(order) and not row.get(f"tp{stage}_notified"):
                            row[f"tp{stage}_notified"] = True
                            changed = True
                            tg(f'✅ V006｜{row["base"]}USDT 到達 TP{stage}')

                    check = self._ma_exit(row)
                    if check and check[-1] != row.get("ma_checked_15m"):
                        period, reason, close, ma, crossed, candle = check
                        row["ma_checked_15m"] = candle
                        row["active_ma"] = period
                        row["active_ma_value"] = ma
                        changed = True
                        if crossed:
                            for name in ("stop_algo_id", "tp2_algo_id", "tp3_algo_id"):
                                self._cancel_algo(sid, row.get(name))
                            fresh_position = self._position(row["symbol"])
                            if fresh_position:
                                signed = num((fresh_position.get("info") or {}).get("positionAmt"))
                                if signed:
                                    self._close_market(row["symbol"], signed, f"{period}日均線退出")
                            row["ma_exit_signal_time"] = candle
                            row["exit_reason"] = f"{period}日均線退出"
                            tg(
                                f'📉 V006｜{row["base"]}USDT 剩餘部位退出\n'
                                f'原因：完成15m收盤跌破／站上 {period} 日線\n'
                                f'15m收盤：{close:.10g}\n均線：{ma:.10g}\n判斷：{reason}'
                            )

            except Exception as exc:
                logging.getLogger("V006").exception("reconcile %s failed", key)
                now = time.time()
                last_alert = num(row.get("last_reconcile_error_alert"))
                # 避免 API 短暫錯誤每次輪詢都洗版；同一持倉最多每 15 分鐘告警一次。
                if now - last_alert >= 900:
                    row["last_reconcile_error_alert"] = now
                    row["last_reconcile_error"] = str(exc)
                    changed = True
                    try:
                        tg(
                            f'⚠️ V006｜{row.get("base", row.get("symbol", "?"))} 實盤管理發生錯誤\n'
                            f'目前不會靜默忽略。\n錯誤：{exc}'
                        )
                    except Exception:
                        pass

        if changed:
            save_state(state)
        return changed
