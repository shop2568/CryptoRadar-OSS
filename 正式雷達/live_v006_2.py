#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.2 實盤執行層。

V006.2 不改訊號、排倉、倉位、4R、TP 或原始結構停損位置。
只修正進場後條件單的可靠性：

1. 原始結構停損仍是必要停損，絕不取消。
2. 移除「條件單 API 一次失敗就立刻市價砍倉」的舊保護行為。
3. 進場後立即確認停損是否真的存在；沒有建立成功就快速補掛。
4. API 只是暫時查不到時，不重複建立，也不誤判成停損消失。
5. 尚未確認停損時留下明確狀態，交由既有 reconcile 持續補掛並告警。

TP1 後原策略既定的含成本保本停損不是本次所稱的「奇怪保護行為」，
仍照 V006.1 的正式出場規則執行。
"""

from __future__ import annotations

import time

from live_v006_1 import LiveTrader as V0061Trader


class LiveTrader(V0061Trader):
    """V006.2：保留真正停損，取消 API 異常時的誤砍倉。"""

    STOP_VERIFY_DELAYS = (0.35, 0.70, 1.20, 2.00)

    def _client_id(self, prefix):
        return ("V0062" + prefix + str(int(time.time() * 1000)))[-32:]

    def _ensure_original_stop(self, row, sid, exit_side):
        """確認原始結構停損存在；只在確定缺失時補掛。

        QUERY_ERROR 代表目前無法確認，不代表停損不存在。這時保留既有
        algoId，避免因查詢短暫失敗建立多張重複停損。
        """
        errors = []
        attempts = 0

        for delay in self.STOP_VERIFY_DELAYS:
            attempts += 1
            algo_id = row.get("stop_algo_id")
            if algo_id:
                order, status, error = self._probe_algo(sid, algo_id)
                if self._active_algo(order):
                    row["stop_order_verified"] = True
                    row["stop_order_retry"] = False
                    row["stop_order_verify_attempts"] = attempts
                    row["stop_order_verified_at"] = time.time()
                    row.pop("stop_order_error", None)
                    row.pop("stop_order_critical", None)
                    return True
                if status == "QUERY_ERROR":
                    errors.append(f"第{attempts}次查詢失敗：{error}")
                    time.sleep(delay)
                    continue
                if self._finished(order):
                    errors.append(f"停損已觸發或結束：{status}")
                    break

                # 交易所明確回報這張單不在工作，才清除編號並補掛。
                errors.append(f"原停損狀態不是工作中：{status}")
                row["stop_algo_id"] = None

            try:
                self._create_original_stop(row, sid, exit_side)
                errors.append(f"第{attempts}次已送出原始結構停損，等待確認")
            except Exception as exc:
                errors.append(f"第{attempts}次建立失敗：{exc}")
            time.sleep(delay)

        # 最後再確認一次，不因最後一次剛建立尚未查詢就誤報。
        algo_id = row.get("stop_algo_id")
        if algo_id:
            order, status, error = self._probe_algo(sid, algo_id)
            if self._active_algo(order):
                row["stop_order_verified"] = True
                row["stop_order_retry"] = False
                row["stop_order_verify_attempts"] = attempts + 1
                row["stop_order_verified_at"] = time.time()
                row.pop("stop_order_error", None)
                row.pop("stop_order_critical", None)
                return True
            if status == "QUERY_ERROR":
                errors.append(f"最後確認查詢失敗：{error}")
            else:
                errors.append(f"最後確認狀態：{status}")

        row["stop_order_verified"] = False
        row["stop_order_retry"] = True
        row["stop_order_critical"] = True
        row["stop_order_verify_attempts"] = attempts + 1
        row["stop_order_error"] = "；".join(errors[-6:])
        return False

    def enter(self, row, state):
        position = super().enter(row, state)
        pending = {**row, **position}
        sid = position.get("exchange_symbol")
        exit_side = "SELL" if row["direction"] == "LONG" else "BUY"
        self._ensure_original_stop(pending, sid, exit_side)

        # 把驗證與補掛後的停損狀態完整帶回正式 state。
        for key in (
            "stop_algo_id",
            "stop_order_verified",
            "stop_order_retry",
            "stop_order_critical",
            "stop_order_verify_attempts",
            "stop_order_verified_at",
            "stop_order_error",
        ):
            if key in pending:
                position[key] = pending[key]
            else:
                position.pop(key, None)
        position["execution_version"] = "V006.2"
        position["pre_tp1_stop_policy"] = "原始結構停損固定不移動"
        position["removed_old_behavior"] = "條件單API異常時立即市價砍倉"
        return position

