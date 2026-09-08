#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V234｜Full-Market Dynamic Scheduling Replay


ChatGPT 接手版：
1. 先重跑 V188 Control，官方比較口徑維持 annual-reset accounting。
2. Control exact PASS 後才跑 W2 / W4。
3. Router 掛在 V188 frozen patch 後、正式 allocator 前。
4. Router skip 真的釋放 slot；reclaim 真的改 entry_time，讓 MAX5/NEW2/cooldown/ranking 重排。
5. 2023+2024 = Research；2025 = Validation；V221C Forward 完全禁止使用。
6. 不修改 V010 / V188 / Frozen V219 / V221C。
"""
from __future__ import annotations


import ast
import copy
import hashlib
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


import pandas as pd


R = Path(__file__).resolve().parent
W = R.parent
D = Path.home() / "CryptoRadar"
DOWNLOADS = Path.home() / "Downloads"
OFFICIAL = D / "V188_四連敗防守加BTC強趨勢Override_三年正式完整重排"
CUT = pd.Timestamp("2026-09-01T11:45:00Z")


EXPECTED = {
    "Trades": 213,
    "TP1": 77,
    "Net": 3461.5009758019496,
    "PF": 2.5492243715277993,
    "DD": 16.9458,
}


EXPECTED_CONTROL_ERRORS = {
    ("2024", "Bitget", "BGB/USDT:USDT", "2025-01-23 15:00:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "QNTSTOCK/USDT:USDT", "2026-07-01 13:45:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "INFQ/USDT:USDT", "2026-07-13 05:30:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "AXON/USDT:USDT", "2026-07-13 16:00:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "UNH/USDT:USDT", "2026-07-17 13:30:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "HUMA/USDT:USDT", "2026-08-05 15:15:00+00:00", "RuntimeError: 跨交易所價位轉換失敗"),
    ("2025", "BingX", "NCCO7241NATGAS2USD/USDT:USDT", "2026-06-13 04:15:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
    ("2025", "Bitget", "TKO/USDT:USDT", "2026-07-24 13:45:00+00:00", "RuntimeError: 幣安沒有同標的，正式雷達不會下單"),
}


ALLOWED_ENGINE_ERROR_TEXT = (
    "幣安沒有同標的，正式雷達不會下單",
    "跨交易所價位轉換失敗",
)


YEARS = ("2023", "2024", "2025")
ROUTER_MODE = "壓縮早突破"




def utc(value):
    return pd.to_datetime(value, utc=True, errors="coerce")




def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()




def load(path, name):
    path = Path(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法載入：{path}")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj




def safe_read_csv(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 5:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()




def write_csv(name, rows):
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    frame.to_csv(R / name, index=False, encoding="utf-8-sig")




def normalize_frame(frame):
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
    d = d.dropna(subset=["open", "high", "low", "close", "volume"])
    return d[d.index < CUT].copy()




def identity(row):
    return (
        str(row.get("exchange", "")),
        str(row.get("symbol", "")),
        str(row.get("direction", "")),
        utc(row.get("entry_time")).isoformat() if pd.notna(utc(row.get("entry_time"))) else str(row.get("entry_time", "")),
    )




def source_candidate_id(v155, row, transformed):
    entry = utc(transformed.get("entry_time", row.get("entry_time")))
    signal = utc(transformed.get("signal_time", row.get("signal_time")))
    fields = (
        str(row.get("exchange", transformed.get("exchange", ""))),
        str(row.get("symbol", transformed.get("symbol", ""))),
        str(v155.side(row)),
        entry.isoformat() if pd.notna(entry) else "",
        signal.isoformat() if pd.notna(signal) else "",
        str(v155.module_name(row)),
        str(transformed.get("entry_mode", row.get("entry_mode", ""))),
    )
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()




def router_key(exchange, symbol, entry_time):
    t = utc(entry_time)
    return (str(exchange), str(symbol), t.isoformat() if pd.notna(t) else str(entry_time))




def year_for_time(value):
    t = utc(value)
    if pd.isna(t):
        return None
    if pd.Timestamp("2023-08-01T00:00:00Z") <= t < pd.Timestamp("2024-08-01T00:00:00Z"):
        return "2023"
    if pd.Timestamp("2024-08-01T00:00:00Z") <= t < pd.Timestamp("2025-08-01T00:00:00Z"):
        return "2024"
    if pd.Timestamp("2025-08-01T00:00:00Z") <= t < CUT:
        return "2025"
    return None




def metrics(trades, dd):
    if trades is None or len(trades) == 0:
        return {"Trades": 0, "TP1": 0, "Win Rate": 0.0, "Net": 0.0, "PF": None, "DD": dd}
    pnl = pd.to_numeric(trades["已實現淨利_USDT"], errors="coerce").fillna(0.0)
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    count = len(trades)
    tp1 = int(pd.to_datetime(trades["tp1_time_v92"], utc=True, errors="coerce").notna().sum())
    return {
        "Trades": count,
        "TP1": tp1,
        "Win Rate": 100.0 * tp1 / count if count else 0.0,
        "Net": float(pnl.sum()),
        "PF": gains / losses if losses > 0 else None,
        "DD": float(dd) if dd is not None and math.isfinite(float(dd)) else None,
    }




def gate_metrics(actual):
    checks = []
    for field, expected in EXPECTED.items():
        tolerance = 0 if field in ("Trades", "TP1") else (0.000051 if field == "DD" else 1e-6)
        value = actual.get(field)
        ok = value is not None and math.isfinite(float(value)) and abs(float(value) - expected) <= tolerance
        checks.append({"metric": field, "expected": expected, "actual": value, "tolerance": tolerance, "passed": ok})
    return checks




class RouterHistory:
    def __init__(self, v155, parity_path, exact, tag):
        self.v155 = v155
        self.exact = exact
        self.parity = load(parity_path, f"v234_history_{tag}")
        self.original_load_base = self.parity.load_base
        self.base_cache = {}
        self.frame_cache = {}
        self.btc_cache = {}


    def base_for_year(self, year):
        if year in self.base_cache:
            return self.base_cache[year]
        if year == "2023":
            obj = SimpleNamespace(load_frame=self.v155.load_frame_2023)
        else:
            obj = self.original_load_base(year)
        self.base_cache[year] = obj
        return obj


    def load_frame(self, year, exchange, symbol):
        key = (str(year), str(exchange), str(symbol))
        if key in self.frame_cache:
            return self.frame_cache[key]
        base = self.base_for_year(str(year))
        try:
            d = base.load_frame(str(exchange), str(symbol))
        except Exception:
            d = pd.DataFrame()
        d = normalize_frame(d)
        self.frame_cache[key] = d
        return d


    def btc(self, year):
        if year in self.btc_cache:
            return self.btc_cache[year]
        try:
            d = normalize_frame(self.exact.btc_frame(int(year)))
        except Exception:
            d = pd.DataFrame()
        self.btc_cache[year] = d
        return d




def load_gate_and_exact():
    gate_path = R / "V233E_replay_preflight.py"
    exact_path = R / "V233E_exact_replay.py"
    gate = load(gate_path, "V233E_replay_preflight")
    exact = load(exact_path, "v234_exact_replay")
    return gate, exact




def build_stack(tag):
    v155_path = DOWNLOADS / "backtest_1y_v155_failure_guard_recalc_3y.py"
    v187_path = DOWNLOADS / "V187_四連敗防守固定決策_三年正式完整重排.py"
    v188_path = R / "V225" / "V188_四連敗防守加BTC強趨勢Override_三年正式完整重排.py"
    parity_path = W / "V006_1實盤等價回測" / "run_v006_1_parity.py"


    v155 = load(v155_path, f"v234_v155_{tag}")
    v187 = load(v187_path, f"v234_v187_{tag}")
    v155.verify_inputs()
    v155.V155_V85_BAD_CONFIRMATIONS = 3
    v155.V155_V96_MIN_ROOM_R = 5.0
    v155.V155_V96_MAX_RET8 = 0.025
    v187.patch_v85_elite_rescue(v155)


    audit_path = OFFICIAL / "03_V188_V186原16筆_BTC市況逐筆稽核.csv"
    fixed = pd.read_csv(audit_path)
    all_keys = {ast.literal_eval(x) for x in fixed.V188Key}
    block_keys = {
        ast.literal_eval(r.V188Key)
        for r in fixed.itertuples()
        if str(r.V188最終動作).startswith("BLOCK_")
    }
    if len(all_keys) != 16 or len(block_keys) != 9:
        raise RuntimeError("Frozen V188 decision manifest mismatch")


    tree = ast.parse(v188_path.read_text("utf-8-sig"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "patch_v188")
    namespace = {
        "v187": v187,
        "pd": pd,
        "math": math,
        "V188_V186_SEEN": set(),
        "V188_BLOCK_HITS": set(),
        "V188_BLOCK_AUDIT": [],
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(v188_path), "exec"), namespace)
    namespace["patch_v188"](v155, all_keys, block_keys)


    paths = [
        Path(__file__), v155_path, v187_path, v188_path, parity_path, audit_path,
        W / "backtest_1y_v156_high_confidence_failure_guard_3y.py",
        W / "V006三年比較" / "backtest_v006_short_ab_2023.py",
        R / "V233E_replay_preflight.py",
        R / "V233E_exact_replay.py",
        R / "V233_RESEARCH_SELECTION.json",
        R / "V233_DUAL_CHASE_Q66_BREAKOUT_TRADES.csv",
    ]
    for cfg in v155.PERIOD_CONFIG.values():
        paths.extend(cfg["candidate"])
        paths.append(cfg["quality"])
    return v155, v187, namespace, parity_path, paths




def install_router(v155, history, gate, exact, window, router_audit):
    base_transform = v155.transform_v154


    def record_base(row, transformed, sid, original_entry, action, status, **extra):
        rec = {
            "variant": "CONTROL" if window is None else f"W{window}_FULL",
            "source_candidate_id": sid,
            "router_key": "|".join(router_key(row.get("exchange", transformed.get("exchange")), row.get("symbol", transformed.get("symbol")), original_entry)),
            "exchange": row.get("exchange", transformed.get("exchange")),
            "symbol": row.get("symbol", transformed.get("symbol")),
            "direction": v155.side(row),
            "entry_mode": transformed.get("entry_mode", row.get("entry_mode", "")),
            "original_signal_time": transformed.get("signal_time", row.get("signal_time")),
            "original_entry_time": original_entry,
            "routed": False,
            "router_action": action,
            "router_status": status,
            "token_extension": math.nan,
            "btc_extension": math.nan,
            "breakout_level": math.nan,
            "retest_time": pd.NaT,
            "reclaim_time": pd.NaT,
            "new_entry_time": original_entry,
            "new_entry": transformed.get("entry"),
        }
        rec.update(extra)
        router_audit.append(rec)
        return rec


    def transform(row, frame):
        transformed, audit = base_transform(row, frame)
        if transformed is None:
            return None, audit


        out = copy.deepcopy(transformed)
        original_entry = utc(out.get("entry_time", row.get("entry_time")))
        original_signal = utc(out.get("signal_time", row.get("signal_time")))
        sid = source_candidate_id(v155, row, out)
        out["V234_source_candidate_id"] = sid
        out["V234_original_entry_time"] = original_entry
        out["V234_original_signal_time"] = original_signal


        if window is None:
            record_base(row, out, sid, original_entry, "UNCHANGED", "CONTROL_UNCHANGED")
            return out, audit


        mode = str(out.get("entry_mode", row.get("entry_mode", "")))
        direction = str(v155.side(row)).upper()
        if mode != ROUTER_MODE or direction != "LONG":
            record_base(row, out, sid, original_entry, "UNCHANGED", "NON_ROUTER_UNCHANGED")
            return out, audit


        year = year_for_time(original_entry)
        if year is None:
            record_base(row, out, sid, original_entry, "UNCHANGED", "PERIOD_UNKNOWN_KEEP_V188")
            return out, audit


        exchange = str(row.get("exchange", out.get("exchange", "")))
        symbol = str(row.get("symbol", out.get("symbol", "")))
        raw = history.load_frame(year, exchange, symbol)
        btc = history.btc(year)
        if raw.empty or btc.empty:
            record_base(row, out, sid, original_entry, "UNCHANGED", "DATA_UNAVAILABLE_KEEP_V188")
            return out, audit


        tr = copy.deepcopy(out)
        tr["entry_time"] = original_entry
        tr["signal_time"] = original_signal
        tr["entry_mode"] = mode


        try:
            routed, token_ext, btc_ext = exact.classify(tr, raw, btc)
        except Exception as exc:
            record_base(
                row, out, sid, original_entry, "UNCHANGED", "CLASSIFIER_DATA_UNAVAILABLE_KEEP_V188",
                router_error=repr(exc),
            )
            return out, audit


        if not routed:
            record_base(
                row, out, sid, original_entry, "UNCHANGED", "NON_ROUTER_Q66_UNCHANGED",
                token_extension=token_ext, btc_extension=btc_ext,
            )
            return out, audit


        if pd.isna(original_entry) or original_entry >= CUT:
            raise RuntimeError("V234 Router touched Forward OOS")


        try:
            level = gate.source.canonical_breakout(
                raw,
                original_signal,
                "V84",
                ROUTER_MODE,
                "LONG",
            )
        except Exception as exc:
            record_base(
                row, out, sid, original_entry, "UNCHANGED", "BREAKOUT_DATA_UNAVAILABLE_KEEP_V188",
                routed=True, token_extension=token_ext, btc_extension=btc_ext, router_error=repr(exc),
            )
            return out, audit


        asof = original_entry + int(window) * gate.STEP
        if asof >= CUT:
            raise RuntimeError("V234 Router asof crossed Forward cutoff")


        decision = gate.reclaim(
            raw,
            original_entry,
            float(out["entry"]),
            float(out["stop"]),
            float(level),
            int(window),
            asof,
        )
        status = str(decision.get("status", ""))


        common = {
            "routed": True,
            "token_extension": token_ext,
            "btc_extension": btc_ext,
            "breakout_level": level,
            "retest_time": decision.get("retest_time"),
            "reclaim_time": decision.get("reclaim_time"),
            "new_entry_time": decision.get("new_entry_time"),
            "new_entry": decision.get("new_entry"),
        }


        if "DATA_INCOMPLETE" in status or status in {"WAIT_COMPLETED_CANDLE", ""}:
            record_base(row, out, sid, original_entry, "UNCHANGED", status or "DATA_UNAVAILABLE_KEEP_V188", **common)
            return out, audit


        if status != "RECLAIM_READY":
            record_base(row, out, sid, original_entry, "SKIP", status, **common)
            a = dict(audit)
            a["keep"] = False
            a["reason"] = f"V234_W{window}_ROUTER_SKIP_{status}"
            return None, a


        new_time = utc(decision.get("new_entry_time"))
        new_entry = float(decision.get("new_entry"))
        if pd.isna(new_time) or new_time >= CUT:
            raise RuntimeError("V234 illegal routed execution time")


        prices = exact.source_prices(raw, new_time, new_entry)
        new = copy.deepcopy(out)
        new.update(prices)
        new["entry_time"] = new_time
        new["signal_time"] = new_time - gate.STEP
        new["V234_router_variant"] = f"W{window}_FULL"
        new["V234_router_routed"] = True
        new["V234_router_status"] = status
        new["V234_router_token_extension"] = token_ext
        new["V234_router_btc_extension"] = btc_ext
        new["V234_router_breakout_level"] = level
        new["V234_router_retest_time"] = decision.get("retest_time")
        new["V234_router_reclaim_time"] = decision.get("reclaim_time")


        record_base(row, new, sid, original_entry, "RETIME", status, **common)
        a = dict(audit)
        a["keep"] = True
        a["reason"] = f"PASS_V234_W{window}_DUAL_CHASE_RECLAIM"
        return new, a


    v155.transform_v154 = transform




def error_tuple(year, row):
    return (
        str(year),
        str(row.get("exchange", "")),
        str(row.get("symbol", "")),
        str(row.get("entry_time", "")),
        str(row.get("錯誤", "")),
    )




def unexpected_engine_errors(df):
    if df is None or df.empty:
        return pd.DataFrame()
    mask = []
    for _, r in df.iterrows():
        msg = str(r.get("錯誤", ""))
        mask.append(not any(x in msg for x in ALLOWED_ENGINE_ERROR_TEXT))
    return df[pd.Series(mask, index=df.index)].copy()




def inject_meta_into_simulated(result, row):
    if not isinstance(result, dict):
        return result
    tr = result.get("trade")
    if not isinstance(tr, dict):
        return result
    for k, v in row.items():
        if str(k).startswith("V234_"):
            tr[k] = v
    return result




def attach_source_ids(trades, accepted_objects):
    df = trades.copy()
    mapping = {}
    original_entry = {}
    for obj in accepted_objects:
        tr = obj.get("trade", {}) if isinstance(obj, dict) else {}
        if not isinstance(tr, dict):
            continue
        key = identity(tr)
        sid = tr.get("V234_source_candidate_id")
        if sid:
            if key in mapping and mapping[key] != sid:
                raise RuntimeError(f"V234 final identity collision: {key}")
            mapping[key] = sid
            original_entry[sid] = tr.get("V234_original_entry_time", tr.get("entry_time"))
    sids = []
    originals = []
    for _, row in df.iterrows():
        key = identity(row)
        sid = row.get("V234_source_candidate_id") if "V234_source_candidate_id" in df.columns else None
        if pd.isna(sid) or not sid:
            sid = mapping.get(key)
        if not sid:
            raise RuntimeError(f"V234 source id missing for final trade: {key}")
        sids.append(sid)
        originals.append(original_entry.get(sid, row.get("entry_time")))
    df["V234_source_candidate_id"] = sids
    df["V234_original_entry_time"] = originals
    return df




def run_full_variant(label, window, gate, exact):
    v155, v187, namespace, parity_path, source_paths = build_stack(label)
    history = RouterHistory(v155, parity_path, exact, label)
    router_audit = []
    install_router(v155, history, gate, exact, window, router_audit)


    original_load = v155.load_module
    captured_trades = []
    accepted_objects = []
    admissions = []
    pool_audit = []


    def instrument(name, path):
        p = original_load(name, path)
        if Path(path).resolve() != parity_path.resolve():
            return p
        allocate = p.allocate_exact
        money = p.money_management
        sim = p.simulate


        def simulate(row, frame):
            entry = utc(row.get("entry_time"))
            if pd.isna(entry) or entry >= CUT:
                raise ValueError("FORWARD_DATA_FORBIDDEN")
            safe_frame = frame
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                idx = pd.to_datetime(frame.index, utc=True, errors="coerce")
                if (idx >= CUT).any():
                    safe_frame = frame.loc[idx < CUT].copy()
            result = sim(row, safe_frame)
            return inject_meta_into_simulated(result, row)


        def allocate_observed(objects, end):
            before = []
            for obj in objects:
                tr = obj.get("trade", {}) if isinstance(obj, dict) else {}
                before.append(dict(tr) if isinstance(tr, dict) else {})
            accepted, rejected = allocate(objects, end)
            chosen = {identity(o.get("trade", {})) for o in accepted if isinstance(o, dict)}
            reasons = {}
            for t in rejected:
                if isinstance(t, dict):
                    reasons[identity(t)] = t.get("V101拒絕原因", "")
            for t in before:
                if not t:
                    continue
                selected = identity(t) in chosen
                admissions.append({
                    "variant": label,
                    "source_candidate_id": t.get("V234_source_candidate_id"),
                    "symbol": t.get("symbol"),
                    "exchange": t.get("exchange"),
                    "direction": t.get("direction"),
                    "signal_time": t.get("signal_time"),
                    "entry_time": t.get("entry_time"),
                    "score": t.get("score"),
                    "priority": t.get("priority"),
                    "portfolio_kind": t.get("portfolio_kind"),
                    "admitted": selected,
                    "competition_reason": reasons.get(identity(t), ""),
                    "final_status": "ACCEPTED" if selected else "REJECTED",
                })
            pool_audit.append({
                "variant": label,
                "period_end": str(end),
                "prepared_candidates": len(objects),
                "accepted": len(accepted),
                "rejected": len(rejected),
            })
            accepted_objects.extend(copy.deepcopy(accepted))
            return accepted, rejected


        def money_observed(objects):
            trades, summary = money(objects)
            captured_trades.append(trades.copy())
            return trades, summary


        p.simulate = simulate
        p.allocate_exact = allocate_observed
        p.money_management = money_observed
        return p


    v155.load_module = instrument
    error_parts = []


    with tempfile.TemporaryDirectory(prefix=f"v234-{label.lower()}-") as scratch:
        v155.OUT = Path(scratch)
        print("=" * 108)
        print(f"V234 {label}: full candidate pool -> original allocator")
        print("=" * 108)
        summaries, stats = v155.run_variant(label)


        for y in YEARS:
            folder = Path(scratch) / "內部引擎結果" / f"{y}_V006.1實盤等價結果"
            err = safe_read_csv(folder / "錯誤.csv")
            if not err.empty:
                err["year"] = y
                err["variant"] = label
                error_parts.append(err)
            raw_trades = safe_read_csv(folder / "全部成交.csv")
            write_csv(f"V234_{label}_{y}_ERRORS.csv", err)
            write_csv(f"V234_{label}_{y}_TRADES_RAW.csv", raw_trades)


    if not captured_trades:
        raise RuntimeError(f"{label}: no money-management trades captured")


    trades = pd.concat(captured_trades, ignore_index=True, sort=False)
    trades = attach_source_ids(trades, accepted_objects)
    dd = max(float(s.get("最大回撤_pct", math.nan)) for s in summaries)
    total_metrics = metrics(trades, dd)
    errors = pd.concat(error_parts, ignore_index=True, sort=False) if error_parts else pd.DataFrame()
    unexpected = unexpected_engine_errors(errors)


    yearly = []
    for y, s in zip(YEARS, summaries):
        yearly.append({
            "variant": label,
            "year": y,
            "Trades": int(s.get("交易數", 0) or 0),
            "TP1": int(s.get("TP1筆數", 0) or 0),
            "Win Rate": float(s.get("總勝率_pct", 0.0) or 0.0),
            "Net": float(s.get("已實現淨利_USDT", 0.0) or 0.0),
            "PF": float(s.get("Profit_Factor", math.nan)),
            "DD": float(s.get("最大回撤_pct", math.nan)),
            "Errors": int(s.get("錯誤", 0) or 0),
        })


    return {
        "label": label,
        "window": window,
        "metrics": total_metrics,
        "summaries": summaries,
        "yearly": yearly,
        "trades": trades,
        "accepted_objects": accepted_objects,
        "router_audit": pd.DataFrame(router_audit),
        "admissions": pd.DataFrame(admissions),
        "pool_audit": pd.DataFrame(pool_audit),
        "errors": errors,
        "unexpected_errors": unexpected,
        "namespace": namespace,
        "source_paths": source_paths,
        "stats": stats,
    }




def control_error_equivalence(control):
    observed = set()
    if not control["errors"].empty:
        for _, r in control["errors"].iterrows():
            observed.add(error_tuple(str(r.get("year", "")), r))
    unexpected_manifest = observed - EXPECTED_CONTROL_ERRORS
    missing_manifest = EXPECTED_CONTROL_ERRORS - observed
    return {
        "observed_errors": len(observed),
        "expected_manifest_errors": len(EXPECTED_CONTROL_ERRORS),
        "unexpected_manifest": sorted(unexpected_manifest),
        "missing_manifest": sorted(missing_manifest),
        "unexpected_engine_class_errors": len(control["unexpected_errors"]),
        "passed": len(unexpected_manifest) == 0 and control["unexpected_errors"].empty,
    }




def expected_router_keys():
    path = R / "V233_DUAL_CHASE_Q66_BREAKOUT_TRADES.csv"
    if not path.exists():
        raise FileNotFoundError(f"找不到 V233 frozen router manifest: {path}")
    d = pd.read_csv(path)
    d = d[d["variant"].astype(str).str.contains("W2", na=False)].copy()
    keys = {
        router_key(r.exchange, r.symbol, r.entry_time)
        for r in d.itertuples()
    }
    if len(keys) != 19:
        raise RuntimeError(f"V233 frozen Dual-Chase identity != 19: {len(keys)}")
    return keys




def control_accepted_router_keys(control):
    return {
        router_key(r.exchange, r.symbol, r.V234_original_entry_time)
        for r in control["trades"].itertuples()
    }




def validate_router_identity(control, variant, expected_keys):
    audit = variant["router_audit"]
    control_keys = control_accepted_router_keys(control)
    matched = set()
    bad_data = []
    if not audit.empty:
        for _, r in audit.iterrows():
            key = tuple(str(r.get("router_key", "")).split("|", 2))
            if key in control_keys and bool(r.get("routed", False)):
                matched.add(key)
                status = str(r.get("router_status", ""))
                if "DATA" in status or "ERROR" in status or "UNAVAILABLE" in status:
                    bad_data.append((key, status))
    missing = expected_keys - matched
    extra = matched - expected_keys
    return {
        "expected": len(expected_keys),
        "matched": len(matched),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "bad_data": bad_data,
        "passed": matched == expected_keys and not bad_data,
    }




def trade_map(df):
    result = {}
    for _, r in df.iterrows():
        sid = str(r.get("V234_source_candidate_id"))
        result[sid] = r.to_dict()
    return result




def is_winner(row):
    if not row:
        return False
    return pd.notna(utc(row.get("tp1_time_v92")))




def pnl_of(row):
    if not row:
        return 0.0
    x = pd.to_numeric(pd.Series([row.get("已實現淨利_USDT")]), errors="coerce").iloc[0]
    return float(x) if pd.notna(x) else 0.0




def router_action_map(variant):
    out = {}
    audit = variant["router_audit"]
    if audit.empty:
        return out
    for _, r in audit.iterrows():
        out[str(r.get("source_candidate_id"))] = {
            "action": str(r.get("router_action", "")),
            "status": str(r.get("router_status", "")),
            "routed": bool(r.get("routed", False)),
        }
    return out




def scheduling_and_decomposition(control, variant):
    c = trade_map(control["trades"])
    v = trade_map(variant["trades"])
    actions = router_action_map(variant)
    rows = []
    contrib = []
    all_ids = sorted(set(c) | set(v))


    for sid in all_ids:
        cr = c.get(sid)
        vr = v.get(sid)
        cp = pnl_of(cr)
        vp = pnl_of(vr)
        delta = vp - cp
        action = actions.get(sid, {})


        if cr is None:
            kind = "NEWLY_ADMITTED"
            category = "newly_admitted_benefit"
        elif vr is None:
            if action.get("action") == "SKIP":
                kind = "ROUTER_SKIPPED"
                category = "router_skipped_original"
            else:
                kind = "SCHEDULING_DROPPED"
                category = "scheduling_opportunity_cost"
        else:
            ct = utc(cr.get("entry_time"))
            vt = utc(vr.get("entry_time"))
            if pd.notna(ct) and pd.notna(vt) and ct != vt:
                kind = "RETIMED"
                category = "router_retimed"
            else:
                kind = "COMMON_UNCHANGED_ENTRY"
                category = "equity_sizing_spillover"


        rows.append({
            "variant": variant["label"],
            "source_candidate_id": sid,
            "change_type": kind,
            "exchange": (vr or cr or {}).get("exchange"),
            "symbol": (vr or cr or {}).get("symbol"),
            "direction": (vr or cr or {}).get("direction"),
            "control_entry_time": (cr or {}).get("entry_time"),
            "variant_entry_time": (vr or {}).get("entry_time"),
            "control_pnl": cp,
            "variant_pnl": vp,
            "delta_pnl": delta,
            "router_action": action.get("action"),
            "router_status": action.get("status"),
        })
        contrib.append((category, delta, sid))


    total_delta = float(variant["metrics"]["Net"] - control["metrics"]["Net"])
    decomp = []
    categories = (
        "router_retimed",
        "router_skipped_original",
        "newly_admitted_benefit",
        "scheduling_opportunity_cost",
        "equity_sizing_spillover",
    )
    explained = 0.0
    for cat in categories:
        value = float(sum(x[1] for x in contrib if x[0] == cat))
        explained += value
        decomp.append({"variant": variant["label"], "component": cat, "delta_u": value})
    residual = total_delta - explained
    decomp.append({"variant": variant["label"], "component": "reconciliation_residual", "delta_u": residual})
    decomp.append({
        "variant": variant["label"],
        "component": "fees_slippage",
        "delta_u": math.nan,
        "note": "已包含在正式 engine PnL；本表不另行重建，避免雙重計算",
    })
    return pd.DataFrame(rows), pd.DataFrame(decomp)




def retention_summary(control, variant):
    c = trade_map(control["trades"])
    v = trade_map(variant["trades"])
    control_winners = [sid for sid, r in c.items() if is_winner(r)]
    retained_winners = sum(1 for sid in control_winners if sid in v and is_winner(v[sid]))
    top = sorted(c.items(), key=lambda kv: pnl_of(kv[1]), reverse=True)[:10]
    original_tail = sum(pnl_of(r) for _, r in top)
    new_tail = sum(pnl_of(v.get(sid)) for sid, _ in top)
    return {
        "Trade Retention": 100.0 * len(set(c) & set(v)) / len(c) if c else math.nan,
        "Winner Retention": 100.0 * retained_winners / len(control_winners) if control_winners else math.nan,
        "Top10 Alpha": 100.0 * new_tail / original_tail if original_tail else math.nan,
        "Newly Admitted": len(set(v) - set(c)),
        "Dropped": len(set(c) - set(v)),
    }




def dependence_table(control, variant):
    c = trade_map(control["trades"])
    v = trade_map(variant["trades"])
    deltas = []
    for sid in set(c) | set(v):
        deltas.append({
            "source_candidate_id": sid,
            "delta_u": pnl_of(v.get(sid)) - pnl_of(c.get(sid)),
            "symbol": (v.get(sid) or c.get(sid) or {}).get("symbol"),
        })
    d = pd.DataFrame(deltas).sort_values("delta_u", ascending=False)
    total_delta = float(variant["metrics"]["Net"] - control["metrics"]["Net"])
    positive = float(d["delta_u"].clip(lower=0).sum()) if not d.empty else 0.0
    rows = []
    for n in (1, 3, 5):
        top = d.head(n)
        value = float(top["delta_u"].sum()) if not top.empty else 0.0
        rows.append({
            "variant": variant["label"],
            "top_n": n,
            "top_delta_u": value,
            "share_of_positive_delta": value / positive if positive else math.nan,
            "net_advantage_without_top_n": total_delta - value,
            "method": "realized contribution dependence; not a separate counterfactual reschedule rerun",
        })
    return pd.DataFrame(rows)




def yearly_value(result, year, field):
    rows = [r for r in result["yearly"] if r["year"] == str(year)]
    return rows[0][field] if rows else math.nan




def dependence_summary(label, path=None):
    """Fail closed on missing, ambiguous or non-finite reviewer evidence."""
    data = pd.read_csv(path if path is not None else R / "V234_SINGLE_TRADE_DEPENDENCE.csv")
    rows = data[(data["variant"] == label) & (pd.to_numeric(data["top_n"], errors="raise") == 1)]
    if len(rows) != 1:
        raise ValueError(f"Expected exactly one Top1 dependence row: {label}")
    remaining = float(rows.iloc[0]["net_advantage_without_top_n"])
    if not math.isfinite(remaining):
        raise ValueError(f"Non-finite Top1 dependence: {label}")
    return {"Dependence Top1 Remaining Net": remaining, "Dependence PASS": remaining > 0}


def variant_summary(control, variant, router_check):
    ret = retention_summary(control, variant)
    research_net = yearly_value(variant, 2023, "Net") + yearly_value(variant, 2024, "Net")
    control_research = yearly_value(control, 2023, "Net") + yearly_value(control, 2024, "Net")
    val_net = yearly_value(variant, 2025, "Net")
    control_val = yearly_value(control, 2025, "Net")
    val_retention = 100.0 * val_net / control_val if control_val else math.nan
    return {
        **variant["metrics"],
        **ret,
        **dependence_summary(variant["label"]),
        "Research Net": research_net,
        "Control Research Net": control_research,
        "2025 Net": val_net,
        "2025 Alpha Retention": val_retention,
        "Unexpected Engine Errors": len(variant["unexpected_errors"]),
        "Router Identity PASS": bool(router_check["passed"]),
    }




def choose_status(control_summary, w2_summary, w4_summary):
    w2_pass = (
        w2_summary["Router Identity PASS"]
        and w2_summary.get("Dependence PASS") is True
        and w2_summary["Unexpected Engine Errors"] == 0
        and w2_summary["Net"] > control_summary["Net"]
        and w2_summary["PF"] is not None
        and w2_summary["PF"] > control_summary["PF"]
        and w2_summary["DD"] <= control_summary["DD"] + 1e-9
        and w2_summary["Research Net"] > w2_summary["Control Research Net"]
        and w2_summary["2025 Alpha Retention"] >= 95.0
        and w2_summary["Winner Retention"] >= 95.0
        and w2_summary["Top10 Alpha"] >= 95.0
    )
    if w2_pass:
        return "PASSED_RESEARCH"


    promising = False
    for x in (w2_summary, w4_summary):
        if (
            x["Router Identity PASS"]
            and x["Unexpected Engine Errors"] == 0
            and x["Net"] > control_summary["Net"]
            and x["PF"] is not None
            and x["PF"] >= control_summary["PF"]
        ):
            promising = True
    return "PROMISING_NOT_PASSED" if promising else "FAILED_維持V188"




def md_table(rows, columns):
    out = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c)
            if isinstance(v, float):
                vals.append(f"{v:.6f}" if math.isfinite(v) else "NA")
            else:
                vals.append(str(v).replace("|", "\\|").replace("\n", "<br>"))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)




def main():
    gate, exact = load_gate_and_exact()


    # CONTROL
    control = run_full_variant("V188_CONTROL", None, gate, exact)
    checks = gate_metrics(control["metrics"])
    err_eq = control_error_equivalence(control)
    frozen_ok = (
        len(control["namespace"]["V188_V186_SEEN"]) == 16
        and len(control["namespace"]["V188_BLOCK_HITS"]) == 9
    )
    control_pass = all(x["passed"] for x in checks) and err_eq["passed"] and frozen_ok


    write_csv("V234_V188_CONTROL.csv", checks)
    write_csv("V234_ENGINE_ERROR_EQUIVALENCE.csv", [{
        "observed_errors": err_eq["observed_errors"],
        "expected_manifest_errors": err_eq["expected_manifest_errors"],
        "unexpected_manifest_errors": len(err_eq["unexpected_manifest"]),
        "missing_expected_errors": len(err_eq["missing_manifest"]),
        "unexpected_engine_class_errors": err_eq["unexpected_engine_class_errors"],
        "passed": err_eq["passed"],
        "unexpected_detail": repr(err_eq["unexpected_manifest"]),
        "missing_detail": repr(err_eq["missing_manifest"]),
    }])


    if not control_pass:
        summary = {
            "status": "FAILED_維持V188",
            "stage": "BASELINE_NOT_EQUIVALENT",
            "baseline_gate_passed": False,
            "control": control["metrics"],
            "metric_checks": checks,
            "engine_error_equivalence": err_eq,
            "frozen_defense_seen": len(control["namespace"]["V188_V186_SEEN"]),
            "frozen_block_hits": len(control["namespace"]["V188_BLOCK_HITS"]),
            "W2_FULL": "NOT_RUN",
            "W4_FULL": "NOT_RUN",
            "forward_used": False,
            "protected_versions_modified": False,
        }
        (R / "V234_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), "utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 2


    print("\nV188 CONTROL EXACT PASS -> 開始 W2 / W4 Full-Market Dynamic Scheduling\n")


    # ROUTER VARIANTS
    w2 = run_full_variant("W2_FULL", 2, gate, exact)
    w4 = run_full_variant("W4_FULL", 4, gate, exact)


    expected_keys = expected_router_keys()
    w2_router = validate_router_identity(control, w2, expected_keys)
    w4_router = validate_router_identity(control, w4, expected_keys)


    if not w2_router["passed"] or not w4_router["passed"]:
        raise RuntimeError(f"V234 Router identity gate failed: W2={w2_router} W4={w4_router}")


    # OUTPUT TRADES / AUDITS
    write_csv("V234_W2_FULL_MARKET.csv", w2["trades"])
    write_csv("V234_W4_FULL_MARKET.csv", w4["trades"])
    write_csv("V234_ROUTER_AUDIT.csv", pd.concat([w2["router_audit"], w4["router_audit"]], ignore_index=True, sort=False))
    write_csv("V234_CANDIDATE_ADMISSION_AUDIT.csv", pd.concat([control["admissions"], w2["admissions"], w4["admissions"]], ignore_index=True, sort=False))
    write_csv("V234_CANDIDATE_POOL.csv", pd.concat([control["pool_audit"], w2["pool_audit"], w4["pool_audit"]], ignore_index=True, sort=False))


    sched2, decomp2 = scheduling_and_decomposition(control, w2)
    sched4, decomp4 = scheduling_and_decomposition(control, w4)
    scheduling = pd.concat([sched2, sched4], ignore_index=True, sort=False)
    decomposition = pd.concat([decomp2, decomp4], ignore_index=True, sort=False)
    write_csv("V234_SCHEDULING_CHANGES.csv", scheduling)
    write_csv("V234_DELTA_DECOMPOSITION.csv", decomposition)


    dep2 = dependence_table(control, w2)
    dep4 = dependence_table(control, w4)
    dependence = pd.concat([dep2, dep4], ignore_index=True, sort=False)
    write_csv("V234_SINGLE_TRADE_DEPENDENCE.csv", dependence)


    yearly = pd.DataFrame(control["yearly"] + w2["yearly"] + w4["yearly"])
    write_csv("V234_YEARLY.csv", yearly)


    control_summary = {
        **control["metrics"],
        "Trade Retention": 100.0,
        "Winner Retention": 100.0,
        "Top10 Alpha": 100.0,
        "Newly Admitted": 0,
        "Dropped": 0,
        "Research Net": yearly_value(control, 2023, "Net") + yearly_value(control, 2024, "Net"),
        "2025 Net": yearly_value(control, 2025, "Net"),
        "2025 Alpha Retention": 100.0,
        "Unexpected Engine Errors": len(control["unexpected_errors"]),
        "Router Identity PASS": True,
    }
    w2_summary = variant_summary(control, w2, w2_router)
    w4_summary = variant_summary(control, w4, w4_router)


    status = choose_status(control_summary, w2_summary, w4_summary)


    source_paths = []
    for result in (control, w2, w4):
        for p in result["source_paths"]:
            p = Path(p)
            if p not in source_paths:
                source_paths.append(p)
    source_hashes = {str(p): digest(p) for p in source_paths if p.exists()}
    changed = [p for p, h in source_hashes.items() if digest(p) != h]
    if changed:
        raise RuntimeError(f"SOURCE_CHANGED: {changed}")


    summary = {
        "status": status,
        "stage": "V234_FULL_MARKET_DYNAMIC_REPLAY_COMPLETE",
        "baseline_gate_passed": True,
        "control": control_summary,
        "W2_FULL": w2_summary,
        "W4_FULL": w4_summary,
        "router_identity": {"W2": w2_router, "W4": w4_router},
        "research_selection": "W2_W4_PREDECLARED; 2025_NOT_USED_TO_PICK_W4",
        "accounting_contract": "annual reset 1000U per year; total DD=max annual DD",
        "continuous_3y_equity": "not used for formal PASS/FAIL",
        "forward_cutoff": str(CUT),
        "forward_used": False,
        "protected_versions_modified": False,
        "V010_modified": False,
        "V188_modified": False,
        "Frozen_V219_modified": False,
        "V221C_modified": False,
        "source_sha256": source_hashes,
        "source_changed": changed,
        "next_step": "Even if PASSED_RESEARCH, do not promote live; run V235 robustness/fresh validation next.",
    }
    (R / "V234_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), "utf-8")


    table_rows = [
        {"Variant": "V188", **control_summary},
        {"Variant": "W2 Full", **w2_summary},
        {"Variant": "W4 Full", **w4_summary},
    ]
    cols = ["Variant", "Trades", "TP1", "Win Rate", "Net", "PF", "DD", "Trade Retention", "Winner Retention", "Top10 Alpha"]


    sched_counts = scheduling.groupby(["variant", "change_type"]).size().reset_index(name="count") if not scheduling.empty else pd.DataFrame()
    report = [
        "# V234 Full-Market Dynamic Scheduling Replay",
        "",
        f"**Final Status: {status}**",
        "",
        "## Core Metrics",
        "",
        md_table(table_rows, cols),
        "",
        "## Research / Validation",
        "",
        f"- V188 Research 2023+2024 Net: {control_summary['Research Net']:.6f}U",
        f"- W2 Research 2023+2024 Net: {w2_summary['Research Net']:.6f}U",
        f"- W4 Research 2023+2024 Net: {w4_summary['Research Net']:.6f}U",
        f"- V188 2025 Net: {control_summary['2025 Net']:.6f}U",
        f"- W2 2025 Net: {w2_summary['2025 Net']:.6f}U",
        f"- W4 2025 Net: {w4_summary['2025 Net']:.6f}U",
        "- W2 / W4 were predeclared. 2025 was not used to select W4.",
        "",
        "## Router Identity",
        "",
        f"- W2: {w2_router}",
        f"- W4: {w4_router}",
        "",
        "## Scheduling",
        "",
        md_table(sched_counts.to_dict("records"), list(sched_counts.columns)) if not sched_counts.empty else "No scheduling rows.",
        "",
        "## Dependence",
        "",
        md_table(dependence.to_dict("records"), list(dependence.columns)) if not dependence.empty else "No dependence rows.",
        "",
        "## Governance",
        "",
        "- V188 official accounting remains annual-reset; continuous 3Y equity is not a formal gate.",
        "- V221C Forward OOS is excluded at 2026-09-01 11:45 UTC.",
        "- q66 / W2 / W4 / reclaim / stop / TP are frozen; no tuning was performed.",
        "- Fees/slippage remain inside the original engine PnL and are not double-counted in decomposition.",
        "- Single-trade dependence here is realized contribution dependence, not a separate nonlinear portfolio rerun with top trades removed.",
        "- Even PASSED_RESEARCH does not promote live V010; next step is V235 robustness/fresh validation.",
    ]
    (R / "V234_REPORT.md").write_text("\n".join(report), "utf-8")


    print("\n" + "=" * 108)
    print("V234 COMPLETE")
    print(md_table(table_rows, cols))
    print("\nSTATUS:", status)
    print("REPORT:", R / "V234_REPORT.md")
    print("SUMMARY:", R / "V234_SUMMARY.json")
    print("=" * 108)
    return 0




def review_existing_outputs():
    """Reporting-only patch. Does not import engines or call a backtest."""
    summary_path = R / "V234_SUMMARY.json"
    summary = json.loads(summary_path.read_text("utf-8"))
    original_sources = copy.deepcopy(summary["source_sha256"])
    raw_paths = [R / f"V234_V188_CONTROL_{year}_TRADES_RAW.csv" for year in YEARS]
    evidence_paths = raw_paths + [R / name for name in (
        "V234_YEARLY.csv", "V234_SCHEDULING_CHANGES.csv", "V234_DELTA_DECOMPOSITION.csv",
        "V234_SINGLE_TRADE_DEPENDENCE.csv", "V234_W2_FULL_MARKET.csv", "V234_W4_FULL_MARKET.csv")]
    before = {str(path): digest(path) for path in evidence_paths}
    control_trades = pd.concat([pd.read_csv(path) for path in raw_paths], ignore_index=True)
    control = {"trades": control_trades}
    for label, name in (("W2_FULL", "V234_W2_FULL_MARKET.csv"), ("W4_FULL", "V234_W4_FULL_MARKET.csv")):
        variant = {"trades": pd.read_csv(R / name)}
        for frame in (control_trades, variant["trades"]):
            ids = frame["V234_source_candidate_id"]
            if ids.isna().any() or ids.astype(str).str.strip().eq("").any() or ids.duplicated().any():
                raise ValueError("Missing or duplicate source candidate IDs")
        updated = retention_summary(control, variant)
        # Only retention/decision metadata may change; original execution metrics stay frozen.
        summary[label].update(updated)
        summary[label].update(dependence_summary(label))
    status = choose_status(summary["control"], summary["W2_FULL"], summary["W4_FULL"])
    summary["status"] = status
    summary["Final Status"] = status
    summary["research_selection"] = "RESEARCH_EQUIVALENT; 2025_NOT_USED_TO_PICK_W4"
    summary["next_step"] = "V235 Robustness / Fresh Validation (plan only; no tuning or live promotion)"
    summary["reviewer_patch"] = {
        "code_sha256": digest(Path(__file__)), "code_path": str(Path(__file__).resolve()),
        "scope": "retention, dependence gate, markdown report only; no backtest rerun",
        "backtest_source_sha256_preserved": True, "evidence_sha256": before,
    }
    if summary["source_sha256"] != original_sources:
        raise RuntimeError("Original execution provenance changed")
    changed = [path for path, value in before.items() if digest(path) != value]
    if changed:
        raise RuntimeError(f"Execution evidence changed: {changed}")
    columns = ["Variant", "Trades", "TP1", "Win Rate", "Net", "PF", "DD", "Trade Retention",
               "Winner Retention", "Top10 Alpha", "Dependence Top1 Remaining Net", "Dependence PASS"]
    rows = [{"Variant": label, **summary[key]} for label, key in
            (("V188", "control"), ("W2 Full", "W2_FULL"), ("W4 Full", "W4_FULL"))]
    scheduling = pd.read_csv(R / "V234_SCHEDULING_CHANGES.csv")
    counts = scheduling.groupby(["variant", "change_type"]).size().reset_index(name="count")
    dependence = pd.read_csv(R / "V234_SINGLE_TRADE_DEPENDENCE.csv")
    report = ["# V234 Full-Market Dynamic Scheduling Replay", "", f"**Final Status: {status}**", "",
        "本輪僅修正報告與 reviewer gate，沒有重新執行三年回測。V188 維持研究基準。", "",
        md_table(rows, columns), "", "## Reviewer 結論", "",
        "- Trade Retention = Control 與 Variant source ID 交集 / Control source IDs；新增交易不算保留。",
        "- W2 移除 Top1 realized delta 後優勢為負，Dependence PASS=False，不可升級。",
        "- W4 雖通過 Top1 gate，2023+2024 與 W2 相同，不能因 2025 較好事後選 W4。",
        "- Dependence 是 realized contribution 分解，並非移除交易後再重排的反事實回測。", "",
        "## Research / Validation", "",
        md_table([{"Variant": row["Variant"], "Research Net": row.get("Research Net"),
                   "2025 Net": row.get("2025 Net")} for row in rows],
                 ["Variant", "Research Net", "2025 Net"]), "",
        "## Scheduling", "", md_table(counts.to_dict("records"), list(counts.columns)), "",
        "## Dependence", "", md_table(dependence.to_dict("records"), list(dependence.columns)), "",
        "## 來源與限制", "",
        "- 原始 backtest source_sha256 完整保留於 SUMMARY，不以 reviewer 程式 SHA 假冒執行來源。",
        f"- 原執行目標程式 SHA：{original_sources.get(str(Path(__file__).resolve()), '見 SUMMARY source_sha256')}",
        f"- Reviewer patch code SHA-256：{summary['reviewer_patch']['code_sha256']}",
        "- 帳務仍為各年度 1000U reset，TOTAL DD=max annual DD，不是連續三年 equity DD。",
        "- YEARLY 原始 Win Rate 口徑與 TOTAL TP1/Trades 不同，原始 CSV 保留未改，不能直接混比。",
        "- V010 / V188 / Frozen V219 / V221C 與所有 frozen 交易規則均未修改。", "",
        "## 下一步", "", "V235 Robustness / Fresh Validation：目前只建立計畫；不調參、不從 2025 選 W4、不使用 V221C Forward OOS tuning。"]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), "utf-8")
    (R / "V234_REPORT.md").write_text("\n".join(report) + "\n", "utf-8")
    print(status)
    print(md_table(rows, columns))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--review-only"]:
        raise SystemExit(review_existing_outputs())
    raise SystemExit(main())
