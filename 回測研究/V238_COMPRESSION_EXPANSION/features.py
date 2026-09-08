import numpy as np
import pandas as pd


def calculate(row, frame):
    at=pd.Timestamp(row['entry_time']); signal=pd.Timestamp(row['signal_time'])
    if at.tz is None or signal.tz is None or frame.index.tz is None:
        raise ValueError('TIMEZONE_REQUIRED')
    if signal+pd.Timedelta(minutes=15)>at:
        raise ValueError('INCOMPLETE_SIGNAL')
    d=frame.loc[frame.index<=signal,['open','high','low','close','volume']].tail(38).copy()
    if len(d)<38 or d.index[-1]!=signal or d.index.has_duplicates or not (d.index.to_series().diff().dropna()==pd.Timedelta(minutes=15)).all():
        raise ValueError('SEQUENCE_DATA_MISSING')
    if not np.isfinite(d.to_numpy()).all() or (d.volume<0).any():raise ValueError('INVALID_OHLCV')
    if (d.high<d[['open','close','low']].max(axis=1)).any() or (d.low>d[['open','close','high']].min(axis=1)).any():raise ValueError('INVALID_OHLC')
    sign=1 if row['direction']=='LONG' else -1 if row['direction']=='SHORT' else 0
    if not sign:raise ValueError('DIRECTION_REQUIRED')
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    pre=d.iloc[:-1];b=d.iloc[-1];atr=tr.iloc[-21:-1].mean();vr=b.high-b.low
    if atr<=0 or vr<=0:raise ValueError('ZERO_RANGE_OR_ATR')
    out={'quality_status':'COMPLETE','signal_known_at':(signal+pd.Timedelta(minutes=15)).isoformat()}
    for n in [8,16]:
        w=pre.tail(n)
        out[f'contraction_{n}']=tr.iloc[-1-n:-1].median()/atr
        out[f'realized_range_{n}']=(w.high.max()-w.low.min())/atr
        out[f'volatility_width_{n}']=4*w.close.std(ddof=0)/atr
    ratio=tr.iloc[-6:-1].mean()/atr
    c=out['contraction_8']
    out.update(atr5_atr20=ratio,compression='STRONG_COMPRESSION' if c<.5 else 'MILD_COMPRESSION' if c<1 else 'EXPANDING_ALREADY' if ratio>1 else 'NO_COMPRESSION')
    baseline=pre.volume.iloc[-26:-6].mean(); med=pre.volume.tail(20).median();base30=pre.volume.iloc[-36:-6].mean()
    if min(baseline,med,base30)<=0:raise ValueError('VOLUME_REFERENCE_MISSING')
    v3=pre.volume.tail(3).mean()/baseline;v6=pre.volume.tail(6).mean()/baseline
    rising=0
    for change in pre.volume.tail(7).diff().dropna().iloc[::-1]:
        if change<=0:break
        rising+=1
    bv=b.volume/med
    volume=('EXHAUSTED_HIGH_VOLUME' if v6>=2 and pre.volume.tail(3).mean()<pre.volume.iloc[-6:-3].mean() else
            'HEALTHY_BUILD' if v3>1 and v6>1 and rising>=2 else
            'SUDDEN_SPIKE_ONLY' if v3<=1 and bv>=2 else 'FLAT')
    body=sign*(b.close-b.open)/vr;clv=(b.close-b.low)/vr if sign==1 else (b.high-b.close)/vr
    uw=(b.high-max(b.open,b.close))/vr;lw=(min(b.open,b.close)-b.low)/vr
    wick=uw if sign==1 else lw
    structure=pre.high.tail(16).max() if sign==1 else pre.low.tail(16).min()
    beyond=sign*(b.close-structure)/atr; expansion=tr.iloc[-1]/atr
    quality=('WICK_REJECTION' if wick>=.5 or clv<=.25 else
             'OVERSIZED_EXHAUSTION' if expansion>=3 else
             'CLEAN_EXPANSION' if body>=.5 and clv>=.75 and beyond>0 and expansion>=1 else 'WEAK_BREAK')
    compressed=out['compression'] in ('STRONG_COMPRESSION','MILD_COMPRESSION')
    transition=('REJECTION_BREAKOUT' if quality=='WICK_REJECTION' else
                'SPIKE_BREAKOUT' if volume!='HEALTHY_BUILD' and (bv>=2 or expansion>=3) else
                'NO_BASE_BREAKOUT' if not compressed else
                'HEALTHY_TRANSITION' if volume=='HEALTHY_BUILD' and quality=='CLEAN_EXPANSION' else 'WEAK_TRANSITION')
    out.update(volume_build_3v20=v3,volume_build_6v20=v6,volume_build_6v30=pre.volume.tail(6).mean()/base30,
               rising_volume_count=rising,high_volume_count=int((pre.volume.tail(6)>baseline).sum()),volume_state=volume,
               signed_body_ratio=body,body_ratio=abs(body),signed_clv=clv,upper_wick_ratio=uw,lower_wick_ratio=lw,
               adverse_wick_ratio=wick,breakout_range_atr=expansion,breakout_distance_atr=beyond,
               breakout_close_beyond_structure=sign*(b.close-structure),breakout_volume_ratio=bv,
               quality=quality,transition=transition)
    return out
