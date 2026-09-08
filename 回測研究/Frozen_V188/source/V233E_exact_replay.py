"""V233 frozen trigger, original V84 price construction, V213 formal exits.

Fixed-cohort paired execution counterfactual: non-router cash allocations are
frozen by contract. This is not a full candidate-universe portfolio reranking.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import V233E_replay_preflight as gate

R = Path(__file__).resolve().parent
W = R.parent
D = Path(r'C:\Users\Ting1\CryptoRadar')
sys.path.insert(0, str(W))
import v213_entry_confirmation as confirmation


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PARITY = W / 'V006_1實盤等價回測/run_v006_1_parity.py'
P = load(PARITY, 'v233e_formal_parity')
B = load(R / 'V225/V225_進場後失敗管理_V188正式固定成交回測.py', 'v233e_baseline_accounting')
FORMAL = SimpleNamespace(live006=SimpleNamespace(BACKTEST_COST_PER_SIDE=P.COST,
    LEVERAGE=20, RISK_PER_TRADE=P.RISK_PER_TRADE, PER_POSITION_NOTIONAL=P.POSITION_NOTIONAL_CAP))
READ_HASHES = {}


def read_cache(path):
    frame,meta,digest=gate.source.load_cache(path)
    if str(path) in READ_HASHES and READ_HASHES[str(path)] != digest:
        raise ValueError('Raw cache changed during replay')
    READ_HASHES[str(path)]=digest
    return frame,meta,digest


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_prices(frame, when, price):
    """Execute the original V84 stop statements and V79 TP expressions.

    AST extraction avoids importing old scripts with import-time output writes,
    and avoids calling their obsolete exit simulator or future audit labels.
    No original formula/constant is changed. Input ends BEFORE execution.
    """
    hist = frame[frame.index < when].tail(64).copy()
    if len(hist) < 21 or not hist.index.equals(pd.date_range(hist.index[0], periods=len(hist), freq='15min')):
        raise ValueError('Incomplete price-construction warmup')
    av = float(confirmation.atr(hist).iloc[-1])
    ns = {'d': hist, 'j': len(hist)-1, 'lookback': 20, 'atr': av,
          'entry': price, 'rr': 4.0, 'float': float, 'min': min, 'max': max}
    tree = ast.parse((W/'backtest_1y_v84_pre_pump_entry.py').read_text('utf-8-sig'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'candidates')
    for target in ('structural_low', 'stop'):
        nodes = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == target for t in n.targets)]
        if len(nodes) != 1:
            raise ValueError('V84 source fragment ambiguous')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<V84 original stop>', 'exec'), ns)
    stop = float(ns['stop'])
    if not 0 < stop < price:
        raise ValueError('Invalid regenerated structural stop')
    ns['risk'] = price-stop
    tree = ast.parse((W/'backtest_1y_v79_pump_first_pullback.py').read_text('utf-8-sig'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'simulate')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'tp1' for t in n.targets))
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<V79 original TP1>', 'exec'), ns)
    ret = next(n for n in fn.body if isinstance(n, ast.Return)).value
    targets = {}
    for key, value in zip(ret.keys, ret.values):
        if isinstance(key, ast.Constant) and key.value in ('tp1', 'tp2', 'tp3'):
            targets[key.value] = float(eval(compile(ast.Expression(value), '<V79 original TP>', 'eval'), ns))
    return {'entry': price, 'actual_entry': price, 'stop': stop,
            'risk_price': price-stop, 'stop_pct': (price-stop)/price, **targets}


def size_and_simulate(original, frame, decision):
    when = decision['new_entry_time']
    new = copy.deepcopy(original)
    new.update(source_prices(frame, when, decision['new_entry']))
    new.update(entry_time=when, signal_time=when-gate.STEP)
    # This reference equity is the frozen V188 allocation context, NOT equity
    # from modified previous trades: those trades must remain unchanged.
    old_effective = min(P.RISK_PER_TRADE, P.POSITION_NOTIONAL_CAP * original['stop_pct'])
    reference_equity = original['V225_official_risk_u'] / old_effective if old_effective else 0
    old_initial = P.INITIAL_EQUITY
    try:
        P.INITIAL_EQUITY = reference_equity
        trade, events = P.simulate(new, frame)
        money, _ = P.money_management([{'trade': trade, 'events': events}])
    finally:
        P.INITIAL_EQUITY = old_initial
    risk_u = reference_equity * min(P.RISK_PER_TRADE, P.POSITION_NOTIONAL_CAP * new['stop_pct'])
    trade.update(V225_official_risk_u=risk_u, reference_equity=reference_equity,
                 quantity=risk_u/new['risk_price'],
                 已實現淨利_USDT=float(money.iloc[0]['已實現淨利_USDT']))
    if not math.isclose(sum(e['pnl_r'] for e in events)*risk_u, trade['已實現淨利_USDT'], abs_tol=1e-8):
        raise ValueError('New execution accounting does not reconcile')
    last=pd.to_datetime(trade['final_exit_time_v92'],utc=True)
    last=last if pd.notna(last) else frame.index[-1]
    if not pd.date_range(when,last,freq='15min').isin(frame.index).all():
        raise ValueError('DATA_INCOMPLETE execution path')
    return {'trade': trade, 'events': events}


def inputs():
    original = gate.source.read_trades()
    # Some archived column labels are mojibake; identify the unique official
    # realized-USDT column, verify total, never overwrite official sources.
    cols = [c for c in original if c.endswith('USDT')]
    if len(cols) != 1:
        raise ValueError('Official realized-U column ambiguous')
    original['已實現淨利_USDT'] = pd.to_numeric(original[cols[0]], errors='raise')
    if len(original) != 213 or not math.isclose(original['已實現淨利_USDT'].sum(), 3461.5009758019496, abs_tol=1e-7):
        raise ValueError('Official baseline identity failed')
    if pd.to_datetime(original.tp1_time_v92, utc=True).notna().sum() != 77:
        raise ValueError('Official TP1 identity failed')
    raw = pd.read_csv(R/'V231_RAW_SOURCE_AUDIT.csv')
    raw.entry_time = pd.to_datetime(raw.entry_time, utc=True)
    baseline = []
    for i, row in original.iterrows():
        tr = B.canonical_trade(row)
        tr['trade_id'] = i
        fixed, events = B.official_baseline_from_row_v225_v8(tr, FORMAL)
        fixed['quantity'] = fixed['V225_official_risk_u']/abs(fixed['entry']-fixed['stop'])
        baseline.append({'trade': fixed, 'events': events})
    return baseline, raw


def get_frame(trade, raw):
    z = raw[(raw.year == trade['year']) & (raw.exchange == trade['exchange'])
            & (raw.symbol == trade['symbol']) & (raw.entry_time == trade['entry_time'])]
    if len(z) != 1:
        raise ValueError('DATA_INCOMPLETE raw source')
    if pd.isna(z.iloc[0]['cache']):
        if trade['symbol']=='BTC/USDT:USDT' and trade['exchange']=='Binance' and trade['year']==2025:
            path=W/'backtest_1y_v82_core_plus_pump_watch_results/BTC_USDT_15m_更新快取.pkl.gz'
        else:
            rel={2023:'backtest_v006_1_original_2023_upstream_results/historical_cache_15m',
                 2024:'backtest_v145_previous_year_upstream_rebuild_results/historical_cache_15m',
                 2025:'backtest_cache_1y'}[trade['year']]
            h=hashlib.sha256(f"{trade['exchange']}|{trade['symbol']}|15m|365".encode()).hexdigest()
            path=D/rel/('stable_'+h+'.pkl.gz')
    else:
        path=Path(z.iloc[0]['cache'])
    frame, meta, digest = read_cache(path)
    if (meta.get('exchange'), meta.get('symbol')) != (trade['exchange'], trade['symbol']):
        raise ValueError('Source identity mismatch')
    return frame, digest


def btc_frame(year):
    if year==2025:
        return read_cache(W/'backtest_1y_v82_core_plus_pump_watch_results/BTC_USDT_15m_更新快取.pkl.gz')[0]
    rel = {2023:'backtest_v006_1_original_2023_upstream_results/historical_cache_15m',
           2024:'backtest_v145_previous_year_upstream_rebuild_results/historical_cache_15m',
           2025:'backtest_cache_1y'}[year]
    h = hashlib.sha256(b'Binance|BTC/USDT:USDT|15m|365').hexdigest()
    frame,meta,_=read_cache(D/rel/('stable_'+h+'.pkl.gz'))
    if (meta.get('exchange'),meta.get('symbol'))!=('Binance','BTC/USDT:USDT'):
        raise ValueError('BTC raw identity mismatch')
    return frame


def classify(tr, frame, btc):
    # V233 feature snapshot is the last completed bar before the actual V188
    # entry (confirmed against archived IOTA repriced entry), not necessarily
    # the earlier upstream signal_time retained by V154.
    signal = tr['entry_time']-gate.STEP
    if pd.to_datetime(tr['signal_time'], utc=True)>signal or signal+gate.STEP >= gate.CUT:
        raise ValueError('Signal clock mismatch/cutoff')
    if signal not in frame.index or signal not in btc.index or signal-gate.STEP not in btc.index:
        raise ValueError('DATA_INCOMPLETE classifier')
    b = frame.loc[signal]
    token = (float(b.high)-float(b.low))/float(b.open)*100
    btc_return = (float(btc.loc[signal,'close'])/float(btc.loc[signal-gate.STEP,'close'])-1)*100
    return gate.dual_chase(tr['entry_mode'],token,btc_return,gate.frozen_thresholds()), token, btc_return


def replay(base, raw, window, ids=None):
    objects, diffs, audit = [], [], []
    btc = {y:btc_frame(y) for y in (2023,2024,2025)}
    for obj in base:
        tr = obj['trade']
        if ids is not None and tr['trade_id'] not in ids:
            continue
        new = copy.deepcopy(obj)
        routed = False
        token = btc_ret = None
        decision = {'status':'NON_ROUTER_UNCHANGED'}
        if tr['entry_mode'] == '壓縮早突破':
            frame, digest = get_frame(tr,raw)
            routed, token, btc_ret = classify(tr,frame,btc[tr['year']])
            if routed:
                level = gate.source.canonical_breakout(frame,pd.to_datetime(tr['signal_time'],utc=True),'V84','壓縮早突破','LONG')
                decision = gate.reclaim(frame,tr['entry_time'],tr['entry'],tr['stop'],level,window,tr['entry_time']+window*gate.STEP)
                decision['cache_sha256'] = digest
                if 'DATA_INCOMPLETE' in decision['status']:
                    raise ValueError(f"{tr['symbol']}: {decision['status']}")
                new = size_and_simulate(tr,frame,decision) if decision['status']=='RECLAIM_READY' else None
        nt = new['trade'] if new else {}
        if new:
            objects.append(new)
        diff = {'window':window,'trade_id':tr['trade_id'],'year':tr['year'],
                'exchange':tr['exchange'],'symbol':tr['symbol'],'direction':tr['direction'],
                'signal_time':tr['signal_time'],'routed':routed,**decision,
                'token_extension':token,'btc_extension':btc_ret,
                'original_entry_time':tr['entry_time'],'original_pnl':tr['已實現淨利_USDT'],
                'new_pnl':nt.get('已實現淨利_USDT',0),
                'original_winner':pd.notna(pd.to_datetime(tr['tp1_time_v92'],utc=True)),
                'new_winner':pd.notna(pd.to_datetime(nt.get('tp1_time_v92'),utc=True))}
        for field in ('entry','stop','tp1','tp2','tp3','quantity'):
            diff['original_'+field] = tr[field]
            diff['new_'+field] = nt.get(field)
        diff['delta_pnl'] = diff['new_pnl']-diff['original_pnl']
        diff['risk_u'] = nt.get('V225_official_risk_u',0)
        diffs.append(diff)
        if not routed:
            for field in ('entry_time','entry','stop','tp1','tp2','tp3','quantity','已實現淨利_USDT'):
                a,b=tr[field],nt[field]
                equal = (pd.isna(a) and pd.isna(b)) or a==b
                if not equal:
                    raise ValueError(f'NON_ROUTER_CHANGED: {tr["trade_id"]} {field}')
        if routed and new:
            audit.extend({'window':window,'trade_id':tr['trade_id'],**e,
                          'pnl_u':e['pnl_r']*nt['V225_official_risk_u']} for e in new['events'])
    return objects,pd.DataFrame(diffs),pd.DataFrame(audit)


def metrics(objects, years):
    selected=[o for o in objects if o['trade']['year'] in years]
    pnl=pd.Series([o['trade']['已實現淨利_USDT'] for o in selected],dtype=float)
    wins=sum(pd.notna(pd.to_datetime(o['trade'].get('tp1_time_v92'),utc=True)) for o in selected)
    dd=[]
    for y in years:
        annual=[o for o in selected if o['trade']['year']==y]
        if annual:
            _,s=B.frozen_v188_money_management(annual,FORMAL,1000.0)
            dd.append(s['最大回撤_pct'])
    return {'Trades':len(selected),'TP1':wins,'Win Rate':100*wins/len(selected) if selected else None,
            'Net':float(pnl.sum()),'PF':float(pnl[pnl>0].sum()/-pnl[pnl<0].sum()) if (pnl<0).any() else None,
            'Max DD':max(dd,default=0)}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--targeted',action='store_true');args=ap.parse_args()
    base,raw=inputs()
    code_paths=[Path(__file__),Path(gate.__file__),Path(gate.source.__file__),Path(confirmation.__file__),
                PARITY,Path(B.__file__),W/'backtest_1y_v84_pre_pump_entry.py',
                W/'backtest_1y_v79_pump_first_pullback.py',R/'V233_RESEARCH_SELECTION.json']
    before={str(p):sha(p) for p in code_paths}
    before.update({o['trade']['official_path']:sha(o['trade']['official_path']) for o in base})
    if args.targeted:
        keys={'BB/USDT:USDT','CHR/USDT:USDT','ARPA/USDT:USDT'}
        ids={o['trade']['trade_id'] for o in base if o['trade']['symbol'] in keys}
        ids.add(base[0]['trade']['trade_id'])
        rows=[]
        for w in (2,4):
            _,df,_=replay(base,raw,w,ids)
            rows.extend(df.to_dict('records'))
        target=pd.DataFrame(rows)
        required=[(2,'BB/USDT:USDT','RECLAIM_READY'),(4,'CHR/USDT:USDT','RECLAIM_READY'),
                  (2,'CHR/USDT:USDT','NO_RECLAIM_SKIP'),(2,'ARPA/USDT:USDT','NO_RECLAIM_SKIP'),
                  (2,'1000PEPE/USDT:USDT','NON_ROUTER_UNCHANGED')]
        for w,s,state in required:
            if not ((target.window==w)&(target.symbol==s)&(target.status==state)).any():
                raise ValueError('Targeted replay gate failed')
        target.to_csv(R/'V233E_TARGETED_REPLAY.csv',index=False,encoding='utf-8-sig')
        print(pd.DataFrame(rows)[['window','symbol','status','original_pnl','new_pnl']].to_string(index=False))
        return
    if not (R/'V233E_TARGETED_REPLAY.csv').exists():
        raise RuntimeError('Run targeted replay before full replay')
    expected=pd.read_csv(R/'V233_DUAL_CHASE_Q66_BREAKOUT_TRADES.csv')
    expected=expected[expected.variant.str.contains('W2_|W4_')]
    versions={'V188':base}; diffs=[];events=[];rows=[];tails=[];dependencies=[]
    for window in (2,4):
        objects,df,ev=replay(base,raw,window)
        if int(df.routed.sum())!=19:
            raise ValueError('Raw causal classifier differs from frozen 19-signal set')
        exp=expected[expected.variant.str.contains(f'W{window}_')].copy()
        exp.entry_time=pd.to_datetime(exp.entry_time,utc=True)
        actual=df[df.routed].rename(columns={'original_entry_time':'entry_time'})
        joined=actual.merge(exp,on=gate.KEY,validate='one_to_one')
        if len(joined)!=19 or not (joined.status_x.eq('RECLAIM_READY')==joined.active).all():
            raise ValueError('Frozen eligibility differs')
        if ((joined.token_extension-joined.range_pct).abs()>1e-10).any() or ((joined.btc_extension-joined.BTC_15m_return).abs()>1e-10).any():
            raise ValueError('Frozen feature values do not match raw candles')
        versions[f'W{window} Exact']=objects;diffs.append(df);events.append(ev)
        # Export authoritative execution fields only; old candidate audit PnL
        # columns must not masquerade as recalculated exit/fee values.
        fields=['trade_id','year','exchange','symbol','direction','signal_time','entry_time',
                'entry','stop','tp1','tp2','tp3','quantity','V225_official_risk_u',
                'tp1_time_v92','tp2_time_v92','tp3_time_v92','final_exit_time_v92',
                'final_exit_price_v92','final_exit_reason_v92','remaining_pct_v92',
                'realized_net_r_v92','unrealized_net_r_v92','已實現淨利_USDT']
        pd.DataFrame([{k:o['trade'].get(k) for k in fields} for o in objects]).to_csv(R/f'V233E_W{window}_TRADES.csv',index=False,encoding='utf-8-sig')
        positive=df.delta_pnl.clip(lower=0).sum()
        net=df.delta_pnl.sum()
        for n in (1,3,5):
            top=df.nlargest(n,'delta_pnl').delta_pnl.sum()
            dependencies.append({'window':window,'top_n':n,'delta_u':top,
                'share_of_positive_delta':top/positive if positive else None,
                'net_without_top_n':net-top})
        for n in (5,10,20):
            top=df.nlargest(n,'original_pnl')
            tails.append({'window':window,'top_n':n,'original_pnl':top.original_pnl.sum(),
                          'new_pnl':top.new_pnl.sum(),
                          'alpha_retention_pct':100*top.new_pnl.sum()/top.original_pnl.sum(),
                          'winner_retention_pct':100*top.new_winner.sum()/n})
    for version,objects in versions.items():
        for label,years in [('2023',[2023]),('2024',[2024]),('2025',[2025]),
                            ('Research',[2023,2024]),('Total',[2023,2024,2025])]:
            row={'version':version,'period':label,**metrics(objects,years)}
            bm=metrics(base,years)
            row['Trade Retention']=100*row['Trades']/bm['Trades']
            if version=='V188':
                row.update({'Winner Retention':100.0,'Tail Alpha':100.0})
            else:
                d=diffs[int(version[1])//2-1]  # W2 -> 0, W4 -> 1
                d=d[d.year.isin(years)]
                row['Winner Retention']=100*d[d.original_winner].new_winner.sum()/bm['TP1']
                top=d.nlargest(10,'original_pnl')
                row['Tail Alpha']=100*top.new_pnl.sum()/top.original_pnl.sum()
            rows.append(row)
    table=pd.DataFrame(rows);diff=pd.concat(diffs,ignore_index=True)
    table.to_csv(R/'V233E_YEARLY.csv',index=False,encoding='utf-8-sig')
    for window in (2,4):
        table[table.version==f'W{window} Exact'].to_csv(R/f'V233E_W{window}_RESULTS.csv',index=False,encoding='utf-8-sig')
    diff.to_csv(R/'V233E_DIFF_V188.csv',index=False,encoding='utf-8-sig')
    diff[diff.routed].to_csv(R/'V233E_DUAL_CHASE_COUNTERFACTUAL.csv',index=False,encoding='utf-8-sig')
    diff[diff.routed & ~diff.status.eq('RECLAIM_READY')].to_csv(R/'V233E_NO_RECLAIM_SKIPS.csv',index=False,encoding='utf-8-sig')
    pd.concat(events,ignore_index=True).to_csv(R/'V233E_EVENT_ORDER_AUDIT.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(tails).to_csv(R/'V233E_TAIL_ALPHA.csv',index=False,encoding='utf-8-sig')
    table[['version','period','Winner Retention','Tail Alpha']].to_csv(R/'V233E_WINNER_RETENTION.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(dependencies).to_csv(R/'V233E_SINGLE_TRADE_DEPENDENCE.csv',index=False,encoding='utf-8-sig')
    subset=[];recon=[];monthly=[]
    for window,d in zip((2,4),diffs):
        routed=d[d.routed];win=routed.original_winner;active=routed.status.eq('RECLAIM_READY')
        saved=(routed[~win].new_pnl-routed[~win].original_pnl).sum()
        lost=(routed[win].original_pnl-routed[win].new_pnl).sum()
        subset.append({'window':window,'signals':len(routed),'reclaim_entries':int(active.sum()),
            'skips':int((~active).sum()),'winners_preserved':int((win&routed.new_winner).sum()),
            'losers_avoided':int((~win&~active).sum()),'retest_winners':int((active&routed.new_winner).sum()),
            'retest_losers':int((active&~routed.new_winner).sum()),'loss_saved_u':saved,
            'winner_alpha_lost_u':lost,'net_benefit_u':saved-lost})
        variant_net=table[(table.version==f'W{window} Exact')&(table.period=='Total')].iloc[0].Net
        error=float(variant_net-metrics(base,[2023,2024,2025])['Net']-d.delta_pnl.sum())
        if abs(error)>1e-8: raise ValueError('Total reconciliation failed')
        recon.append({'window':window,'net_delta':d.delta_pnl.sum(),'loss_saved':saved,'winner_alpha_lost':lost,
                      'reconciliation_error':error,'non_router_count':int((~d.routed).sum()),
                      'non_router_pnl_delta':float(d[~d.routed].delta_pnl.sum()),
                      'allocation_mode':'FROZEN_BASELINE_ALLOCATION_CONTEXT_NO_PORTFOLIO_RERANK'})
    for version,objects in versions.items():
        t=pd.DataFrame([o['trade'] for o in objects]);t['month']=pd.to_datetime(t.entry_time,utc=True).dt.strftime('%Y-%m')
        for month,g in t.groupby('month'):
            monthly.append({'version':version,'month':month,'trades':len(g),'tp1':int(pd.to_datetime(g.tp1_time_v92,utc=True).notna().sum()),'net_u':g['已實現淨利_USDT'].sum()})
    pd.DataFrame(recon).to_csv(R/'V233E_RECONCILIATION.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(monthly).to_csv(R/'V233E_MONTHLY.csv',index=False,encoding='utf-8-sig')
    a=table[(table.period=='Research')&(table.version=='W2 Exact')].iloc[0]
    b=table[(table.period=='Research')&(table.version=='W4 Exact')].iloc[0]
    equivalent=all(a[c]==b[c] for c in ('Trades','TP1','Net','PF','Max DD'))
    # No promotion from a paired-cohort experiment without independent full
    # portfolio parity. Strong results remain promising, never silently PASS.
    totals=table[(table.period=='Total')&table.version.ne('V188')]
    promising=bool((totals.Net>3461.5009758019496).any())
    summary={'status':'PROMISING_NOT_PASSED' if promising else 'FAILED_維持V188',
        'research_selection':'RESEARCH_EQUIVALENT' if equivalent else 'BOTH_PREDECLARED_REPORTED_NO_VALIDATION_SELECTION',
        'execution_scope':'Fixed V188 cohort, frozen baseline allocation context; NOT full portfolio reranking',
        'max_dd_definition':'max of three independently reset 1000U annual realized event equity drawdowns',
        'metrics':rows,'dual_chase':subset,'single_trade_dependence':dependencies,
        'protected_versions_modified':False,'forward_used':False,'source_sha256':{
            str(p):sha(p) for p in [PARITY,W/'backtest_1y_v84_pre_pump_entry.py',W/'backtest_1y_v79_pump_first_pullback.py',R/'V233_RESEARCH_SELECTION.json']}}
    (R/'V233E_SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),'utf-8')
    hashes={**before,**READ_HASHES}
    changed=[p for p,h in hashes.items() if sha(p)!=h]
    if changed: raise ValueError(f'Source modified during replay: {changed}')
    leak={'status':'PASS_FOR_PAIRED_EXECUTION_SCOPE','q66_refit':False,
        'all_38_frozen_feature_pairs_matched_raw':True,
        'all_38_reclaim_decisions_matched':True,'non_router_changed':0,
        'only_completed_retest_reclaim':True,'execution_at_next_bar_open':True,
        'entry_bar_hlc_not_used_for_entry':True,'no_outcome_in_router':True,
        '2025_window_selection':False,'forward_cutoff':str(gate.CUT),'forward_used':False,
        'source_changed':changed,'source_sha256':hashes,
        'risk_context':'Frozen baseline allocation, U/R reconstruction used for accounting only, not routing',
        'not_certified':'Historical market precision / mark-price paths / full-universe portfolio reranking'}
    (R/'V233E_FUTURE_LEAK_AUDIT.json').write_text(json.dumps(leak,indent=2,ensure_ascii=False),'utf-8')
    print(table[table.period.isin(['Research','2025','Total'])].to_string(index=False))
    print(json.dumps(subset,ensure_ascii=False,indent=2))



if __name__=='__main__':
    main()
