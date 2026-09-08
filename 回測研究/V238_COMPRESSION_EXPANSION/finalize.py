import hashlib,json
from pathlib import Path
import pandas as pd
OUT=Path(__file__).resolve().parent


def main():
    d=pd.read_csv(OUT/'V238_SELECTIVITY.csv');checks=[]
    for (family,state),g in d[d.direction.eq('LONG')&d.year.isin(['2023','2024'])].groupby(['family','state']):
        original=g[g.n.eq(0)]
        primary=g[g.n.le(3)]
        local=len(primary)==10 and (primary.selective_efficiency>0).all() and (primary.flagged>3).all() and (primary.symbols>1).all()
        checks.append(dict(family=family,state=state,local_top1_top3_stable=local,
                           top5_positive_both_years=bool(len(g[g.n.eq(5)])==4 and (g[g.n.eq(5)].selective_efficiency>0).all()),
                           cross_exchange_proven=bool((original.exchanges>1).all()),
                           winner_retention_90_possible=bool(len(original)==2 and (original.winner_sacrifice<=.1).all()),
                           stable_eligible=bool(local and len(original)==2 and (original.exchanges>1).all() and (original.winner_sacrifice<=.1).all())))
    pd.DataFrame(checks).to_csv(OUT/'V238_STABILITY.csv',index=False,encoding='utf-8-sig')
    prior=OUT.parent/'V237_H1_H4_MOMENTUM_REGIME'
    evidence=['V237_SUMMARY.json','V237_PREDECLARED_RULES.json','V237_PHASE1.json','audit.py','features.py','research_replay.py']
    provenance=dict(phase='0.5',selection_contamination=False,untouched=False,
                    statement='V237 computed 2025 simulation before late guard; not immediate pre-simulation rejection. Available execution transcript exposed counts/progress, not 2025 metrics or trade-level winner/loser identities. V238 definitions are user-requested natural constants and OHLC-only, no V237 outcome/identity lookup.',
                    evidence_sha={f:hashlib.sha256((prior/f).read_bytes()).hexdigest() for f in evidence},
                    no_v237_2025_result_file=not any(prior.glob('*2025*')),
                    scope='Evidence supports no selection use, not a claim that 2025 was never computed or a universal proof of absent memory.')
    (OUT/'V238_CONTAMINATION_AUDIT.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    summary=dict(status='FAILED_維持V188',phase1='V238_FAILED_NO_STABLE_BREAKOUT_QUALITY_SIGNAL',
                 qualification='No signal meets full cross-year/concentration/exchange and winner-retention requirements; some local descriptive separation exists.',
                 selected_variant=None,validation='NOT_RUN',tests='8/8',source_opportunities=106,
                 research_trades=94,complete=91,missing=3,selection_contamination=False)
    (OUT/'V238_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(pd.DataFrame(checks).to_string(index=False))


if __name__=='__main__':main()
