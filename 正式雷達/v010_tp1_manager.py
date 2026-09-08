#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V010 TP1 live-management hotfix v2.

Fixes Binance USD-M -4130 after TP1 when an *untracked/orphan* closePosition
conditional order still exists for the same symbol/direction.

Key behavior:
- keeps V009/V010 strategy, Entry, TP1/TP2/TP3 and MA logic unchanged;
- TP1 already FILLED is never submitted again;
- before creating COST-BE stop, queries all current open algo orders for symbol;
- adopts an already-active COSTBE stop if one exists;
- otherwise cancels only active closePosition conditional orders on the exit side;
- quantity/reduceOnly TP orders are NOT cancelled by the targeted cleanup;
- creates exactly one closePosition STOP_MARKET at cost break-even;
- if creation fails, tries to adopt any active stop; otherwise restores original stop;
- fail-closed with CRITICAL marker when no protective stop can be confirmed.
"""
from __future__ import annotations

import math
import time
from typing import Any


class TP1SafeStopReplaceMixin:
    V010_CANCEL_SETTLE_DELAYS = (0.10, 0.20, 0.40, 0.80, 1.20)
    V010_CREATE_RETRY_DELAYS = (0.10, 0.25, 0.50, 1.00)

    @staticmethod
    def _v010_text(exc: BaseException) -> str:
        return str(exc or "")

    @classmethod
    def _v010_order_already_final(cls, exc: BaseException) -> bool:
        text = cls._v010_text(exc).lower()
        return any(x in text for x in (
            "-2011", "-2013", "unknown order", "order does not exist",
            "no such order", "order is final",
        ))

    @classmethod
    def _v010_closeposition_conflict(cls, exc: BaseException) -> bool:
        text = cls._v010_text(exc).lower()
        return "-4130" in text or ("closeposition" in text and "existing" in text)

    @staticmethod
    def _v010_bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v or "").strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _v010_algo_status(order: dict) -> str:
        return str(order.get("algoStatus") or order.get("status") or "").upper()

    @classmethod
    def _v010_algo_active(cls, order: dict) -> bool:
        status = cls._v010_algo_status(order)
        return status in {"NEW", "ACCEPTED", "WORKING", "PARTIALLY_FILLED"}

    @staticmethod
    def _v010_algo_id(order: dict):
        return order.get("algoId") or order.get("strategyId") or order.get("orderId")

    @staticmethod
    def _v010_order_type(order: dict) -> str:
        return str(order.get("orderType") or order.get("type") or order.get("strategyType") or "").upper()

    @staticmethod
    def _v010_client_id(order: dict) -> str:
        return str(order.get("clientAlgoId") or order.get("newClientStrategyId") or order.get("clientOrderId") or "")

    @staticmethod
    def _v010_side(order: dict) -> str:
        return str(order.get("side") or "").upper()

    @staticmethod
    def _v010_trigger(order: dict) -> float:
        try:
            return float(order.get("triggerPrice") or order.get("stopPrice") or 0.0)
        except Exception:
            return 0.0

    def _v010_open_algos(self, sid: str) -> list[dict]:
        """Return current open USD-M algo orders in a response-shape tolerant way."""
        raw = self.ex.fapiPrivateGetOpenAlgoOrders({"symbol": sid})
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            for key in ("orders", "data", "rows", "list"):
                value = raw.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
            # Some API wrappers can return a single object.
            if self._v010_algo_id(raw):
                return [raw]
        return []

    def _v010_close_all_conflicts(self, sid: str, exit_side: str) -> list[dict]:
        out = []
        for order in self._v010_open_algos(sid):
            if not self._v010_algo_active(order):
                continue
            if self._v010_side(order) and self._v010_side(order) != str(exit_side).upper():
                continue
            if self._v010_bool(order.get("closePosition")):
                out.append(order)
        return out

    def _v010_active_stop_orders(self, sid: str, exit_side: str) -> list[dict]:
        return [
            o for o in self._v010_close_all_conflicts(sid, exit_side)
            if self._v010_order_type(o) == "STOP_MARKET"
        ]

    def _v010_cancel_algo_strict(self, sid: str, algo_id: Any) -> dict:
        if not algo_id:
            return {}
        try:
            return self.ex.fapiPrivateDeleteAlgoOrder({"symbol": sid, "algoId": algo_id})
        except Exception as exc:
            if self._v010_order_already_final(exc):
                return {"status": "ALREADY_FINAL", "algoId": algo_id}
            raise

    def _v010_trigger_matches(self, order: dict, desired: Any) -> bool:
        try:
            a = float(self._v010_trigger(order))
            b = float(desired)
            if a <= 0 or b <= 0:
                return False
            return abs(a - b) <= max(1e-12, abs(b) * 1e-7)
        except Exception:
            return False

    def _v010_find_existing_cost_stop(self, sid: str, exit_side: str, desired_trigger: Any):
        for order in self._v010_active_stop_orders(sid, exit_side):
            cid = self._v010_client_id(order).upper()
            if "COSTBE" in cid or self._v010_trigger_matches(order, desired_trigger):
                return order
        return None

    def _v010_cancel_orphan_close_all(self, row: dict, sid: str, exit_side: str, desired_trigger: Any) -> list[Any]:
        """Cancel only closePosition conditional orders that block COSTBE.

        Reduce-only quantity TP2/TP3 orders are intentionally untouched.
        If a COSTBE stop is already active, it is adopted instead of cancelled.
        """
        existing = self._v010_find_existing_cost_stop(sid, exit_side, desired_trigger)
        if existing:
            row["stop_algo_id"] = self._v010_algo_id(existing)
            row["v010_orphan_cleanup"] = "ADOPT_EXISTING_COSTBE"
            return []

        cancelled = []
        conflicts = self._v010_close_all_conflicts(sid, exit_side)
        for order in conflicts:
            aid = self._v010_algo_id(order)
            if not aid:
                continue
            try:
                self._v010_cancel_algo_strict(sid, aid)
                cancelled.append(aid)
            except Exception:
                # Re-query later; don't assume it was cancelled.
                pass
        row["v010_orphan_close_all_found"] = len(conflicts)
        row["v010_orphan_close_all_cancelled"] = list(cancelled)
        return cancelled

    def _v010_wait_all_close_all_clear(self, sid: str, exit_side: str, desired_trigger: Any) -> None:
        for delay in self.V010_CANCEL_SETTLE_DELAYS:
            existing = self._v010_find_existing_cost_stop(sid, exit_side, desired_trigger)
            if existing:
                return
            if not self._v010_close_all_conflicts(sid, exit_side):
                return
            time.sleep(delay)

    def _v010_create_close_stop_with_retry(self, row, sid, exit_side, trigger, prefix, old_stop=None):
        last_exc: BaseException | None = None
        for delay in self.V010_CREATE_RETRY_DELAYS:
            # If a previous request was accepted but the client did not persist it,
            # adopt it instead of creating a duplicate.
            existing = self._v010_find_existing_cost_stop(sid, exit_side, trigger)
            if existing:
                return existing
            try:
                return self._algo(sid, exit_side, "STOP_MARKET", trigger, self._client_id(prefix))
            except Exception as exc:
                last_exc = exc
                if not self._v010_closeposition_conflict(exc):
                    raise
                # Root cause of the current incident: an untracked close-all algo
                # can survive even after the state-tracked stop was cancelled.
                self._v010_cancel_orphan_close_all(row, sid, exit_side, trigger)
                self._v010_wait_all_close_all_clear(sid, exit_side, trigger)
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("V010 無法建立 STOP_MARKET")

    def _v010_any_confirmed_stop(self, row, sid, exit_side):
        """Adopt any active close-all STOP_MARKET as protection."""
        try:
            stops = self._v010_active_stop_orders(sid, exit_side)
        except Exception:
            stops = []
        if not stops:
            return None
        # Prefer COSTBE, otherwise take the first confirmed STOP_MARKET.
        stops.sort(key=lambda o: ("COSTBE" not in self._v010_client_id(o).upper(), self._v010_algo_id(o) or 0))
        chosen = stops[0]
        row["stop_algo_id"] = self._v010_algo_id(chosen)
        return chosen

    def _v010_restore_original_stop(self, row, sid, exit_side, old_stop):
        confirmed = self._v010_any_confirmed_stop(row, sid, exit_side)
        if confirmed:
            return True, "ACTIVE_STOP_ADOPTED"

        try:
            if self._position_quantity(row) <= 0:
                return True, "POSITION_ALREADY_CLOSED"
        except Exception:
            pass

        trigger = self.ex.price_to_precision(row["symbol"], float(row["stop"]))
        try:
            # A stale TAKE_PROFIT close-all can block restoring the stop too.
            self._v010_cancel_orphan_close_all(row, sid, exit_side, desired_trigger=-9.87654321e99)
            self._v010_wait_all_close_all_clear(sid, exit_side, desired_trigger=-9.87654321e99)
            restored = self._algo(sid, exit_side, "STOP_MARKET", trigger, self._client_id("SLRESTORE"))
            row["stop_algo_id"] = restored.get("algoId")
            row["stop_order_retry"] = False
            row["v010_stop_restore"] = "ORIGINAL_STRUCTURE_STOP_RESTORED"
            return True, "ORIGINAL_STRUCTURE_STOP_RESTORED"
        except Exception as restore_exc:
            # Last chance: maybe an order became active between calls.
            confirmed = self._v010_any_confirmed_stop(row, sid, exit_side)
            if confirmed:
                return True, "ACTIVE_STOP_ADOPTED_AFTER_RESTORE_ERROR"
            row["stop_order_critical"] = True
            row["stop_order_retry"] = True
            row["stop_order_error"] = (
                "V010 TP1成本保護建立失敗，且原結構停損恢復失敗：" + str(restore_exc)
            )
            return False, str(restore_exc)

    def _replace_stop_at_entry(self, row, sid, exit_side):
        protection = self._cost_break_even_price(row)
        trigger = self.ex.price_to_precision(row["symbol"], protection)
        old_stop = row.get("stop_algo_id")

        row["v010_tp1_stop_transition"] = "STARTED_V2"
        row["v010_old_stop_algo_id"] = old_stop
        row["v010_tp1_stop_transition_at"] = time.time()

        # First adopt any already-created cost stop from a previous partial run.
        existing = self._v010_find_existing_cost_stop(sid, exit_side, trigger)
        if existing:
            row["stop_algo_id"] = self._v010_algo_id(existing)
            row["post_tp1_protection_price"] = float(trigger)
            row["post_tp1_protection_model"] = "BACKTEST_COST_BE"
            row["v010_tp1_stop_transition"] = "COST_BE_ACTIVE_ADOPTED"
            return existing

        # Cancel state-tracked stop first.
        if old_stop:
            try:
                self._v010_cancel_algo_strict(sid, old_stop)
            except Exception as exc:
                order, _status, _err = self._probe_algo(sid, old_stop)
                if self._active_algo(order):
                    raise RuntimeError(
                        "V010 無法取消原結構停損；原停損仍在，未切換成本保護：" + str(exc)
                    ) from exc
                if not self._v010_order_already_final(exc):
                    # Continue to full open-algo reconciliation instead of trusting
                    # a stale state id.
                    row["v010_tracked_cancel_uncertain"] = str(exc)

        # Crucial v2 step: reconcile *all* current closePosition algos, not only
        # stop_algo_id from local state.
        self._v010_cancel_orphan_close_all(row, sid, exit_side, trigger)
        self._v010_wait_all_close_all_clear(sid, exit_side, trigger)

        # Cleanup may have discovered an already-active COSTBE and adopted it.
        existing = self._v010_find_existing_cost_stop(sid, exit_side, trigger)
        if existing:
            new_stop = existing
        else:
            try:
                new_stop = self._v010_create_close_stop_with_retry(
                    row, sid, exit_side, trigger, "COSTBE", old_stop=old_stop
                )
            except Exception as exc:
                safe, restore_status = self._v010_restore_original_stop(
                    row, sid, exit_side, old_stop
                )
                row["v010_tp1_stop_transition"] = "ROLLBACK_SAFE" if safe else "CRITICAL_UNPROTECTED"
                row["v010_tp1_stop_transition_error"] = str(exc)
                row["v010_tp1_stop_restore_status"] = restore_status
                if safe:
                    raise RuntimeError(
                        "V010 成本保護建立失敗，但已確認有效停損；下一輪會重試。原因：" + str(exc)
                    ) from exc
                raise RuntimeError(
                    "V010 CRITICAL：成本保護與原結構停損都無法確認，請立即人工檢查交易所停損。原因：" + str(exc)
                ) from exc

        row["stop_algo_id"] = self._v010_algo_id(new_stop)
        row["post_tp1_protection_price"] = float(trigger)
        row["post_tp1_protection_model"] = "BACKTEST_COST_BE"
        row["stop_order_retry"] = False
        row.pop("stop_order_error", None)
        row.pop("stop_order_critical", None)
        row["v010_tp1_stop_transition"] = "COST_BE_ACTIVE_V2"
        row["v010_new_stop_algo_id"] = self._v010_algo_id(new_stop)
        row["v010_tp1_stop_transition_done_at"] = time.time()
        return new_stop


def build_live_trader(base_cls):
    if issubclass(base_cls, TP1SafeStopReplaceMixin):
        return base_cls

    class LiveTrader(TP1SafeStopReplaceMixin, base_cls):
        execution_hotfix = "V010_TP1_SAFE_STOP_REPLACE_V2"

    LiveTrader.__name__ = "LiveTrader"
    LiveTrader.__qualname__ = "LiveTrader"
    return LiveTrader
