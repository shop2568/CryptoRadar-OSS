#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V009 美股代幣 Alpaca 原股量能觀察器。

只對交易所官方市場中已被標成 HIGH_CONFIDENCE 的美股代幣生效。
不使用幣名猜測，避免把 HIVE、AIO 等同名加密幣誤綁成美股。

量能判斷（全部只看已完成的 Alpaca IEX 15 分鐘 K 線）：
- 最新已完成 K 線距現在不超過 75 分鐘時才判斷量能；休市或資料過舊中性放行。
- 最新一根量能 / 前 20 根量能中位數 >= 1.20，或最近 3 根平均量能 /
  前 20 根量能中位數 >= 1.15。

本模組只回傳原股量能狀態。V009 執行層規則為：休市與量能不足中性放行，
量能通過加 2 分排序；只有 API、金鑰、K 線數量或量能基準無法驗證時 fail-closed。
一般加密幣完全不受影響。
"""

from __future__ import annotations

import csv
import math
import os
import statistics
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests


CATALOG = Path(__file__).with_name("us_stock_tokens_catalog.csv")
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
LATEST_RATIO_MIN = 1.20
BUILD_3_MIN = 1.15
MAX_FRESH_MINUTES = 75.0
CACHE_SECONDS = 300.0
PRIORITY_BONUS = 2.0


def _norm_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "/" in text:
        base, rest = text.split("/", 1)
        quote_part = rest.split(":", 1)[0]
        return f"{base}/{quote_part}"
    if text.endswith("USDT"):
        return f"{text[:-4]}/USDT"
    return text


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            out[key.strip()] = value.strip()
    return out


@dataclass
class GateResult:
    is_stock_token: bool
    passed: bool
    reason: str
    underlying: str = ""
    latest_volume_ratio: float = float("nan")
    volume_build_3: float = float("nan")
    latest_bar_time: str = ""
    freshness_minutes: float = float("nan")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def execution_decision(result: GateResult) -> tuple[bool, float, str]:
    """把原股量能觀察結果轉為 V009 的固定執行決策。"""
    if not result.is_stock_token:
        return True, 0.0, "一般加密幣"
    if result.passed:
        return True, PRIORITY_BONUS, "原股放量，加2分排序"
    reason = str(result.reason)
    if "資料已過時" in reason:
        return True, 0.0, "原股休市，仍可交易"
    if "量能未提升" in reason:
        return True, 0.0, "原股未放量，維持原排序"
    return False, 0.0, "原股量能資料無法驗證，不進場"


class AlpacaVolumeGate:
    def __init__(self, root: Path, session: requests.Session | None = None):
        self.root = Path(root)
        self.session = session or requests.Session()
        env = _load_env(self.root / ".env")
        self.key = os.getenv("ALPACA_API_KEY", env.get("ALPACA_API_KEY", ""))
        self.secret = os.getenv("ALPACA_API_SECRET", env.get("ALPACA_API_SECRET", ""))
        self.catalog = self._load_catalog()
        self.cache: dict[str, tuple[float, GateResult]] = {}

    def _load_catalog(self) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        if not CATALOG.exists():
            return out
        with CATALOG.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("confidence", "")).upper() != "HIGH_CONFIDENCE":
                    continue
                if str(row.get("active", "")).strip().lower() not in {"1", "true", "yes"}:
                    continue
                venue = str(row.get("exchange", "")).strip().lower()
                underlying = str(row.get("underlying", "")).strip().upper()
                if not venue or not underlying:
                    continue
                for symbol in (row.get("symbol"), row.get("exchange_id")):
                    normalized = _norm_symbol(symbol)
                    if normalized:
                        out[(venue, normalized)] = underlying
        return out

    def classify(self, row: dict[str, Any]) -> str:
        venue = str(row.get("venue") or row.get("exchange") or "").strip().lower()
        source = _norm_symbol(row.get("source_symbol") or row.get("symbol"))
        return self.catalog.get((venue, source), "")

    def _fetch(self, underlying: str, now: datetime) -> GateResult:
        if not self.key or not self.secret:
            return GateResult(True, False, "Alpaca 金鑰未設定，無法確認原股量能", underlying)
        start = now - timedelta(days=10)
        response = self.session.get(
            ALPACA_URL.format(symbol=quote(underlying, safe="")),
            headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret},
            params={
                "timeframe": "15Min",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": now.isoformat().replace("+00:00", "Z"),
                "adjustment": "raw",
                "feed": "iex",
                "sort": "asc",
                "limit": 1000,
            },
            timeout=15,
        )
        response.raise_for_status()
        bars = response.json().get("bars") or []
        completed: list[tuple[datetime, float]] = []
        for bar in bars:
            stamp = pd.to_datetime(bar.get("t"), utc=True, errors="coerce")
            volume = float(bar.get("v") or 0.0)
            if pd.isna(stamp) or volume <= 0:
                continue
            opened = stamp.to_pydatetime()
            if opened + timedelta(minutes=15) <= now - timedelta(minutes=1):
                completed.append((opened, volume))
        if len(completed) < 24:
            return GateResult(True, False, "Alpaca 已完成原股 K 線不足 24 根", underlying)

        latest_time, latest_volume = completed[-1]
        prior = [volume for _, volume in completed[-24:-4]]
        baseline = statistics.median(prior)
        if not math.isfinite(baseline) or baseline <= 0:
            return GateResult(True, False, "Alpaca 原股量能基準無效", underlying)
        latest_ratio = latest_volume / baseline
        build_3 = statistics.mean(volume for _, volume in completed[-3:]) / baseline
        freshness = (now - (latest_time + timedelta(minutes=15))).total_seconds() / 60.0
        common = dict(
            underlying=underlying,
            latest_volume_ratio=latest_ratio,
            volume_build_3=build_3,
            latest_bar_time=latest_time.isoformat(),
            freshness_minutes=freshness,
        )
        if freshness > MAX_FRESH_MINUTES:
            return GateResult(
                True,
                False,
                f"原股量能資料已過時（{freshness:.0f} 分鐘；美股休市時不進美股代幣）",
                **common,
            )
        passed = latest_ratio >= LATEST_RATIO_MIN or build_3 >= BUILD_3_MIN
        reason = (
            f"原股量能確認通過（最新 {latest_ratio:.2f} 倍／近3根 {build_3:.2f} 倍）"
            if passed
            else f"原股量能未提升（最新 {latest_ratio:.2f} 倍／近3根 {build_3:.2f} 倍）"
        )
        return GateResult(True, passed, reason, **common)

    def evaluate(self, row: dict[str, Any], now: datetime | None = None) -> GateResult:
        underlying = self.classify(row)
        if not underlying:
            return GateResult(False, True, "一般加密幣，不套用 Alpaca 閘門")
        now = now or datetime.now(timezone.utc)
        cached = self.cache.get(underlying)
        if cached and time.time() - cached[0] <= CACHE_SECONDS:
            return cached[1]
        try:
            result = self._fetch(underlying, now)
        except Exception as exc:
            result = GateResult(
                True,
                False,
                f"Alpaca 原股量能查詢失敗：{type(exc).__name__}",
                underlying,
            )
        self.cache[underlying] = (time.time(), result)
        return result
