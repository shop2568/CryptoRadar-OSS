"""V233E source and causal-router audit. No trading, network or Forward imports.

This is NOT an execution backtest. Historical outcome fields are used only
after routing for comparison with the supplied frozen V233 artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

import V231_source_trace as source

ROOT = Path(__file__).resolve().parent
CUT = pd.Timestamp('2026-09-01T11:45:00Z')
STEP = pd.Timedelta(minutes=15)
KEY = ['year', 'exchange', 'symbol', 'entry_time']
EXPECTED_RANGE = 1.0729253687222766
EXPECTED_BTC = 0.07674343645047975


def frozen_thresholds():
    data = json.loads((ROOT / 'V233_RESEARCH_SELECTION.json').read_text('utf-8'))
    if (data['range_q66_threshold'], data['btc_q66_threshold']) != (EXPECTED_RANGE, EXPECTED_BTC):
        raise ValueError('FROZEN_THRESHOLD_MISMATCH')
    return data


def dual_chase(mode, token_range, btc_return, config):
    # No trade outcome, MFE, MAE, symbol exception or validation-year argument.
    return (mode == '壓縮早突破'
            and token_range > config['range_q66_threshold']
            and btc_return > config['btc_q66_threshold'])


def reclaim(frame, entry_time, entry, stop, level, window, as_of):
    """Frozen LONG-only Compression definition. Returns decision, not PnL.

    Bar timestamps are opens; reclaim_time is the completion/decision time.
    The next bar's OPEN alone is legal execution information at that time.
    """
    if window not in (2, 4):
        raise ValueError('Only frozen W2/W4 allowed')
    if not 0 < stop < entry or not 0 < level or entry_time >= CUT or as_of >= CUT:
        raise ValueError('Invalid input/cutoff')
    if frame.index.has_duplicates or frame.index.tz is None:
        raise ValueError('Invalid candle index')
    risk = entry - stop
    result = {'status': 'NO_RECLAIM_SKIP', 'retest_time': None, 'reclaim_time': None,
              'new_entry_time': None, 'new_entry': None, 'last_complete_time': None}
    touched = False
    for i in range(window):
        ts = entry_time + i * STEP
        completed = ts + STEP
        if completed > as_of:
            return {**result, 'status': 'WAIT_COMPLETED_CANDLE'}
        if ts not in frame.index:
            return {**result, 'status': 'DATA_INCOMPLETE'}
        bar = frame.loc[ts]
        values = [float(bar[k]) for k in ('open', 'high', 'low', 'close')]
        import math
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError('Invalid OHLC')
        if values[1] < max(values) or values[2] > min(values):
            raise ValueError('Inconsistent OHLC')
        result['last_complete_time'] = completed
        # Cumulative invalidation is causal: stop at FIRST completed hit.
        if float(bar.low) <= stop or float(bar.high) >= entry + 4 * risk:
            return {**result, 'status': 'PRE_RECLAIM_INVALIDATED'}
        if touched and float(bar.close) > level:
            result.update(reclaim_time=completed, new_entry_time=completed)
            if completed not in frame.index:
                return {**result, 'status': 'DATA_INCOMPLETE_NEXT_OPEN'}
            price = float(frame.loc[completed, 'open'])
            if not math.isfinite(price) or price <= stop or price >= entry + 4 * risk:
                return {**result, 'status': 'INVALID_NEXT_OPEN'}
            return {**result, 'status': 'RECLAIM_READY', 'new_entry': price}
        if float(bar.close) <= level and not touched:
            touched = True
            result['retest_time'] = completed
    return result


def run():
    config = frozen_thresholds()
    trades = source.read_trades()
    if len(trades) != 213 or trades.duplicated(KEY).any() or (trades.entry_time >= CUT).any():
        raise ValueError('V188 identity/cutoff failed')
    artifact_path = ROOT / 'V233_DUAL_CHASE_Q66_BREAKOUT_TRADES.csv'
    golden = pd.read_csv(artifact_path)
    golden.entry_time = pd.to_datetime(golden.entry_time, utc=True)
    golden = golden[golden.variant.str.contains('W2_|W4_')].copy()
    if len(golden) != 38 or golden.duplicated(['variant'] + KEY).any():
        raise ValueError('Frozen router sample must be 19 times two')
    raw = pd.read_csv(ROOT / 'V231_RAW_SOURCE_AUDIT.csv')
    raw.entry_time = pd.to_datetime(raw.entry_time, utc=True)
    sources = []
    results = []
    for g in golden.to_dict('records'):
        match = lambda d: d[(d.year == g['year']) & (d.exchange == g['exchange'])
                            & (d.symbol == g['symbol']) & (d.entry_time == g['entry_time'])]
        original = match(trades)
        audit = match(raw)
        if len(original) != 1 or len(audit) != 1:
            raise ValueError(f'Non-unique original/source: {g}')
        row = original.iloc[0]
        cache = Path(audit.iloc[0]['cache'])
        frame, meta, digest = source.load_cache(cache)
        if (meta.get('exchange'), meta.get('symbol')) != (g['exchange'], g['symbol']):
            raise ValueError(f'Raw metadata mismatch: {cache}')
        signal = pd.to_datetime(row.signal_time, utc=True)
        if signal + STEP != g['entry_time'] or row.direction != 'LONG':
            raise ValueError('Unproven signal time/direction')
        level = source.canonical_breakout(frame, signal, 'V84', '壓縮早突破', 'LONG')
        bar = frame.loc[signal]
        token_range = (float(bar.high) - float(bar.low)) / float(bar.open) * 100
        window = 2 if 'W2_' in g['variant'] else 4
        decision = reclaim(frame, g['entry_time'], float(row.entry), float(row.stop),
                           level, window, g['entry_time'] + STEP * window)
        expected_time = pd.to_datetime(g['reclaim_time'], utc=True, errors='coerce')
        actual_time = decision['reclaim_time']
        eligible = decision['status'] == 'RECLAIM_READY'
        match_eligible = eligible == bool(g['active'])
        match_time = not eligible or actual_time == expected_time
        results.append({**{k: g[k] for k in KEY}, 'window': window, 'signal_time': signal,
                        'range_pct_raw': token_range, 'range_pct_frozen': g['range_pct'],
                        'range_matches': abs(token_range - g['range_pct']) < 1e-10,
                        'btc_return_frozen': g['BTC_15m_return'],
                        'btc_raw_verified': False,
                        'breakout_level': level, **decision,
                        'shadow_eligible': g['active'], 'eligibility_matches': match_eligible,
                        'completion_time_matches': match_time,
                        'cache_sha256': digest})
        sources.append({'path': str(cache), 'sha256': digest})
    result = pd.DataFrame(results)
    result.to_csv(ROOT / 'V233E_ROUTER_AUDIT.csv', index=False, encoding='utf-8-sig')
    manifest = {'status': 'PREFLIGHT_ONLY_NOT_EXECUTION_PASSED', 'trades': len(trades),
                'routed_signals': len(golden[KEY].drop_duplicates()), 'checks': len(result),
                'range_matches': int(result.range_matches.sum()),
                'eligibility_matches': int(result.eligibility_matches.sum()),
                'completion_time_matches': int(result.completion_time_matches.sum()),
                'btc_raw_verified': False, 'exact_execution_run': False,
                'forward_used': False, 'threshold_refit': False, 'windows': [2, 4],
                'sources': sorted({s['path']: s for s in sources}.values(), key=lambda s:s['path']),
                'frozen_artifact_sha256': hashlib.sha256(artifact_path.read_bytes()).hexdigest()}
    (ROOT / 'V233E_PREFLIGHT.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), 'utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k != 'sources'}, indent=2))


if __name__ == '__main__':
    run()
