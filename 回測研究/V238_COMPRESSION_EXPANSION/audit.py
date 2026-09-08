import hashlib,json,sys
from pathlib import Path
import pandas as pd
from features import calculate
OUT=Path(__file__).resolve().parent
PREV=OUT.parent/'V236_D1_STRUCTURE_CONTEXT'
sys.path.insert(0,str(PREV))
import research_audit as existing


def stats(d):
    return dict(Trades=len(d),TP1=int(d.tp1_hit.sum()),TP1_rate=d.tp1_hit.mean()*100 if len(d) else None,
                Loss=int(d.net_u.lt(0).sum()),Full_Stop=int(d.full_stop.sum()),Avg_R=d.realized_r.mean(),Net=d.net_u.sum())


def main():
    e=existing.verify_baseline();v,_,_,p,_=e.build_stack('V238_AUDIT');h=e.RouterHistory(v,p,None,'V238_AUDIT')
    records=[]
    for year in ['2023','2024']:
        labels=pd.read_csv(PREV/f'V236_BASELINE_V188_CONTROL_{year}_TRADES_RAW.csv')
        byid={r['V234_source_candidate_id']:r for r in labels.to_dict('records')}
        for raw in h.parity.load_candidates(v.PERIOD_CONFIG[year]['candidate']):
            f=h.load_frame(year,raw['exchange'],raw['symbol'])
            structural=v.side(raw)=='LONG' and any(t in v.module_name(raw).upper() for t in v.TARGET_STRUCTURE_MODULES)
            tr,_=v.transform_v154(raw,f if structural else pd.DataFrame())
            if tr is None:continue
            sid=e.source_candidate_id(v,raw,tr)
            r={k:tr.get(k) for k in ['entry_time','signal_time','entry_mode','exchange','symbol']}
            r.update(year=year,direction=v.side(tr),source_candidate_id=sid,selected=sid in byid)
            try:r.update(calculate(r,f))
            except ValueError as ex:r.update(quality_status='DATA_MISSING',error=str(ex))
            if sid in byid:
                l=byid[sid];r.update(tp1_hit=pd.notna(pd.to_datetime(l['tp1_time_v92'],utc=True)),net_u=float(l['已實現淨利_USDT']),realized_r=float(l['realized_net_r_v92']),full_stop=l['final_exit_reason_v92']=='原停損')
            records.append(r)
        print('COMPLETE_YEAR',year,flush=True)
    data=pd.DataFrame(records);data.to_csv(OUT/'V238_FEATURES.csv',index=False,encoding='utf-8-sig')
    summarize(data)


def summarize(data):
    if not set(data.year.astype(str)).issubset({'2023','2024'}):raise ValueError('VALIDATION_LOCKED')
    selected=data[data.selected.eq(True)];d=selected[selected.quality_status.eq('COMPLETE')]
    groups=[];edges=[]
    for year in ['2023','2024','COMBINED']:
        y=d if year=='COMBINED' else d[d.year.astype(str).eq(year)]
        for side in ['LONG','SHORT','ALL']:
            s=y if side=='ALL' else y[y.direction.eq(side)]
            for family in ['compression','volume_state','quality','transition','entry_mode']:
                for state,b in s.groupby(family):
                    groups.append(dict(year=year,direction=side,family=family,state=state,**stats(b)))
                    for tail,n in [('NONE',0),('WINNER',1),('WINNER',3),('WINNER',5),('LOSER',1),('LOSER',3),('LOSER',5)]:
                        ids=(s[s.net_u.gt(0)].nlargest(n,'net_u') if tail=='WINNER' else s[s.net_u.lt(0)].nsmallest(n,'net_u')).index if n else []
                        z=s.drop(ids);flag=z[family].eq(state);b=z[flag];win=z.tp1_hit.eq(True);loss=z.net_u.lt(0)
                        lr=(flag&loss).sum()/loss.sum() if loss.sum() else float('nan')
                        ws=(flag&win).sum()/win.sum() if win.sum() else float('nan')
                        edges.append(dict(year=year,direction=side,family=family,state=state,tail=tail,n=n,flagged=len(b),symbols=b.symbol.nunique(),exchanges=b.exchange.nunique(),loser_rejection=lr,winner_sacrifice=ws,selective_efficiency=lr-ws,**{k:v for k,v in stats(b).items() if k!='Trades'}))
    pd.DataFrame(groups).to_csv(OUT/'V238_GROUPS.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(edges).to_csv(OUT/'V238_SELECTIVITY.csv',index=False,encoding='utf-8-sig')
    print(pd.DataFrame(groups).query("family=='transition' and direction=='LONG'").to_string(index=False))
    summary=dict(selected=len(selected),complete=len(d),missing=len(selected)-len(d),source_count=len(data),validation_selection_contamination=False,
                 source_sha={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [OUT/'features.py',OUT/'PROTOCOL.md',Path(__file__)]})
    (OUT/'V238_PHASE1.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(summary)


if __name__=='__main__':
    if '--summarize-existing' in sys.argv:
        existing.verify_baseline()
        summarize(pd.read_csv(OUT/'V238_FEATURES.csv'))
    else:main()
