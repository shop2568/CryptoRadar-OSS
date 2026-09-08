#!/usr/bin/env python3
"""CryptoRadar V003: V72.8S live radar plus V78 safe false-stop reentry."""
from __future__ import annotations
import fcntl,json,logging,math,os,signal,time
from pathlib import Path
from typing import Any
import ccxt,pandas as pd,requests
from live_v003 import LiveTrader
import v003_live_engine as v003live

ROOT=Path(__file__).resolve().parent
STATE=Path(os.getenv("STATE_FILE",str(ROOT/"radar_v001_live_state.json")))
SCAN=int(os.getenv("SCAN_INTERVAL","300"));COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","86400"))
SHADOW=os.getenv("V003_SHADOW",os.getenv("V001_SHADOW","0")).strip().lower() in {"1","true","yes","on"}
RUN_ONCE=os.getenv("V003_ONCE",os.getenv("V001_ONCE","0")).strip().lower() in {"1","true","yes","on"}
MAX_POSITIONS=5;CORE_SLOTS=3;FLEX_SLOTS=2;MIN_RR=3.0
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("V003")

def envfile(name):
    out={};p=ROOT/name
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
                k,v=raw.split("=",1);out[k.strip()]=v.strip()
    return out
te=envfile(".env.telegram")
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN",te.get("TELEGRAM_BOT_TOKEN",""))
CHAT=os.getenv("TELEGRAM_CHAT_ID",te.get("TELEGRAM_CHAT_ID",""))
TRADER=None

def f(x,d=0.0):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except (TypeError,ValueError):return d

def tg(msg):
    if not TOKEN or not CHAT:return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
          json={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True},timeout=20).raise_for_status()
    except Exception:log.exception("Telegram failed")

def load_state():
    blank={"signals":{},"positions":{},"history":[]}
    if not STATE.exists():return blank
    try:
        x=json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(x,dict):blank.update(x)
    except Exception:log.exception("State load failed")
    return blank

def save_state(x):
    p=STATE.with_suffix(".tmp");p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8");p.replace(STATE)

def client():
    be=envfile(".env.binance")
    key=os.getenv("BINANCE_API_KEY",be.get("BINANCE_API_KEY",""))
    secret=os.getenv("BINANCE_API_SECRET",be.get("BINANCE_API_SECRET",""))
    if not key or not secret:raise RuntimeError("Binance API credentials missing")
    return ccxt.binanceusdm({"apiKey":key,"secret":secret,"enableRateLimit":True,"timeout":30000,
      "options":{"defaultType":"future","adjustForTimeDifference":True}})

def scan_clients(trade):
    return [
      ("Binance",trade),
      ("Bitget",ccxt.bitget({"enableRateLimit":True,"timeout":30000,"options":{"defaultType":"swap"}})),
      ("BingX",ccxt.bingx({"enableRateLimit":True,"timeout":30000,"options":{"defaultType":"swap"}})),
    ]

def binance_trade_map(ex):
    out={}
    for s,m in ex.load_markets().items():
        if m.get("active") is False or not m.get("swap") or not m.get("linear"):continue
        if str(m.get("quote","")).upper()=="USDT" and str(m.get("settle") or "USDT").upper()=="USDT":
            out.setdefault(base(s),s)
    return out

def translate_to_binance(x,trade_symbol,ex):
    source_entry=f(x.get("entry"))
    ticker=ex.fetch_ticker(trade_symbol)
    live=f(ticker.get("last")) or f(ticker.get("bid")) or f(ticker.get("ask"))
    if min(source_entry,live)<=0:raise RuntimeError("cross-exchange price missing")
    ratio=live/source_entry;y=dict(x)
    y["source_entry"]=source_entry;y["symbol"]=trade_symbol;y["cross_exchange_ratio"]=ratio
    for k in ("entry","stop","tp1","tp2","tp3"):y[k]=f(y[k])*ratio
    y["priority"]=f(y.get("priority"))-2.0
    return y

def base(symbol):
    x=symbol.split(":")[0].split("/")[0].upper()
    return x[4:] if x.startswith("1000") and len(x)>4 else x

def symbols(ex):
    m=ex.load_markets(reload=True);out=[]
    for s,x in m.items():
        if x.get("active") is False or not x.get("swap") or not x.get("linear"):continue
        if str(x.get("quote","")).upper()=="USDT" and str(x.get("settle") or "USDT").upper()=="USDT":out.append(s)
    return sorted(set(out))

def frame(ex,s):
    rows=ex.fetch_ohlcv(s,"15m",limit=1000)
    if len(rows)<850:raise RuntimeError("K line shortage")
    d=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume"])
    for c in ["open","high","low","close","volume"]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d["time"]=pd.to_datetime(d.ts,unit="ms",utc=True)
    d=d.dropna().drop_duplicates("ts").sort_values("ts").set_index("time").drop(columns="ts").copy()
    pc=d.close.shift(1);tr=pd.concat([d.high-d.low,(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1)
    d["atr15"]=tr.rolling(14).mean();d["ema5"]=d.close.ewm(span=5,adjust=False).mean();d["ema10"]=d.close.ewm(span=10,adjust=False).mean()
    d["vr"]=d.volume/d.volume.shift(1).rolling(20).mean()
    d["vol20med"]=d.volume.shift(1).rolling(20).median();d["vr_med"]=d.volume/d.vol20med
    span=(d.high-d.low).replace(0,float("nan"));signed=d.volume*((2*d.close-d.high-d.low)/span).clip(-1,1)
    d["pressure"]=signed.rolling(4).sum()/d.volume.rolling(4).sum()
    return d

def fourhour(d):
    q=d.resample("4h",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    q=q[q.index<pd.Timestamp.now(tz="UTC").floor("4h")].copy()
    pc=q.close.shift(1);tr=pd.concat([q.high-q.low,(q.high-pc).abs(),(q.low-pc).abs()],axis=1).max(axis=1)
    q["atr"]=tr.rolling(14).mean()
    for n in (5,10,20,50):q[f"ema{n}"]=q.close.ewm(span=n,adjust=False,min_periods=n).mean()
    q["bh"]=q.high.shift(1).rolling(4).max();q["bl"]=q.low.shift(1).rolling(4).min()
    q["sl"]=q.low.shift(1).rolling(3).min();q["sh"]=q.high.shift(1).rolling(3).max()
    return q.dropna()

def target(q,side,en,st):
    risk=abs(en-st)
    if risk<=0:return None
    h=q.high;l=q.low
    ph=(h>h.shift(1))&(h>h.shift(2))&(h>h.shift(-1))&(h>h.shift(-2))
    pl=(l<l.shift(1))&(l<l.shift(2))&(l<l.shift(-1))&(l<l.shift(-2))
    if side=="LONG":
        levels=sorted({f(x) for x in q.loc[ph,"high"] if f(x)>en})
        if levels:
            t1=levels[0];rr=(t1-en)/risk
            if rr<4:return None
        else:t1=en+4*risk;rr=4
        more=[x for x in levels if x>t1];t2=more[0] if more else en+max(6,rr+2)*risk;t3=more[1] if len(more)>1 else en+max(8,rr+4)*risk
    else:
        levels=sorted({f(x) for x in q.loc[pl,"low"] if 0<f(x)<en},reverse=True)
        if levels:
            t1=levels[0];rr=(en-t1)/risk
            if rr<4:return None
        else:t1=en-4*risk;rr=4
        more=[x for x in levels if x<t1];t2=more[0] if more else en-max(6,rr+2)*risk;t3=more[1] if len(more)>1 else en-max(8,rr+4)*risk
    return None if min(t1,t2,t3)<=0 else (t1,t2,t3,rr,"REAL" if levels else "DISCOVERY")

def core(s,d,q):
    if len(q)<55:return None
    z=q.iloc[-1];bull=z.ema5>z.ema10>z.ema20;bear=z.ema5<z.ema10<z.ema20
    if not (bull or bear):return None
    cur=d.iloc[-1];en=f(cur.close);atr=f(z.atr);hit=None
    for off in range(2,10):
        box=d.iloc[-off-16:-off]
        if len(box)!=16:continue
        hi=f(box.high.max());lo=f(box.low.min());trig=d.iloc[-off]
        if bull and f(trig.close)>hi*1.001 and f(cur.low)<=hi*1.002 and en>=hi*.998 and cur.ema5>cur.ema10 and en>=cur.ema5:hit=("LONG",hi,lo,off);break
        if bear and f(trig.close)<lo*.999 and f(cur.high)>=lo*.998 and en<=lo*1.002 and cur.ema5<cur.ema10 and en<=cur.ema5:hit=("SHORT",lo,hi,off);break
    if not hit:return None
    side,line,opp,delay=hit;st=opp-.25*atr if side=="LONG" else opp+.25*atr;risk=abs(en-st)
    if en<=0 or atr<=0 or not .8<=risk/en*100<=8:return None
    t=target(q.tail(180),side,en,st)
    if not t:return None
    t1,t2,t3,rr,mode=t;vr=f(cur.vr);pr=f(cur.pressure);ext=abs(en-line)/atr
    score=1000+min(rr,12)*2+min(max(vr-1,0),3)*5+max(0,8-2*max(delay-1,0))-ext*10
    return dict(symbol=s,base=base(s),family="CORE",direction=side,setup="BREAKOUT_RETEST",
      signal_time=d.index[-1].isoformat(),entry=en,stop=st,tp1=t1,tp2=t2,tp3=t3,rr=rr,
      volume=vr,pressure=pr,priority=score,target_mode=mode)

def expansion(s,d,q):
    if len(q)<55:return None
    z=q.iloc[-1];atr=f(z.atr);line=f(z.bl);stopbase=f(z.sh)
    if min(atr,line,stopbase)<=0:return None
    slope=(f(z.ema20)-f(q.ema20.iloc[-2]))/atr
    if not (f(z.close)<=f(z.ema20)+.15*atr and slope<=.10):return None
    ci=len(d)-1;cur=d.iloc[ci];prev=d.iloc[ci-1];en=f(cur.close);setup="";bi=-1
    direct=en<line and f(prev.close)>=line and .05<=(line-en)/atr<=.55 and abs(en-f(cur.open))/atr>=.20 and (f(cur.high)-en)/max(f(cur.high)-f(cur.low),1e-12)>=.60
    if direct:setup="DIRECT_BREAKDOWN";bi=ci
    else:
        for i in range(ci-1,max(1,ci-32)-1,-1):
            if f(d.iloc[i].close)<line and f(d.iloc[i-1].close)>=line and (line-f(d.iloc[i].close))/atr>=.05:bi=i;break
        if bi<0:return None
        def reclaim(i):
            x=d.iloc[i];p=d.iloc[i-1]
            return f(x.high)>=line-.20*atr and f(x.close)<line and f(x.close)<f(p.close) and (f(x.high)-f(x.close))/max(f(x.high)-f(x.low),1e-12)>=.55 and abs(f(x.close)-f(x.open))/atr>=.12
        if any(reclaim(i) for i in range(bi+1,ci)) or not reclaim(ci):return None
        setup="FIRST_PULLBACK_RECLAIM"
    st=stopbase+.25*atr;risk=st-en
    if risk<=0 or not .8<=risk/en*100<=8 or (line-en)/atr>.70:return None
    t=target(q.tail(180),"SHORT",en,st)
    if not t:return None
    t1,t2,t3,rr,mode=t;vr=f(cur.vr);pr=f(cur.pressure)
    if setup=="FIRST_PULLBACK_RECLAIM":
        if not (vr>=1.5 and pr<=-.20):return None
        sp=24
    else:
        if not (4<=rr<=6.5 and vr>=1.1 and pr<=-.15):return None
        sp=14
    delay=ci-bi+1;ext=max(0,(line-en)/atr);score=20+sp+min(rr,12)*2+min(max(vr-1,0),3)*5+max(0,8-2*max(delay-1,0))-ext*10
    return dict(symbol=s,base=base(s),family="EXPANSION",direction="SHORT",setup=setup,
      signal_time=d.index[-1].isoformat(),entry=en,stop=st,tp1=t1,tp2=t2,tp3=t3,rr=rr,
      volume=vr,pressure=pr,priority=score,target_mode=mode)

def analyze(ex,s,venue,base_name):
    d=frame(ex,s)
    target=v003live.engine.Target(venue,ex,s,base_name)
    return v003live.causal_candidate(target,d)

def funding(ex,s):
    try:
        x=ex.fetch_funding_rate(s).get("fundingRate");return None if x is None else f(x)
    except Exception:return None

def zhside(x):return "\u505a\u591a" if x=="LONG" else "\u505a\u7a7a"
def zhfamily(x):return "\u6838\u5fc3\u8a0a\u865f" if x=="CORE" else "\u64f4\u5f35\u8a0a\u865f"
def zhsetup(x):return {"BREAKOUT_RETEST":"\u7a81\u7834\u5f8c\u56de\u8e29","DIRECT_BREAKDOWN":"\u76f4\u63a5\u8dcc\u7834","FIRST_PULLBACK_RECLAIM":"\u7b2c\u4e00\u6b21\u53cd\u5f48\u5931\u6557","SAFE_REENTRY":"\u5047\u505c\u640d\u7ad9\u56de\u91cd\u9032"}.get(x,x)

def message(x):
    fr="\u7121\u8cc7\u6599" if x["funding"] is None else f'{x["funding"]*100:+.4f}%'
    shown_entry=f(x.get("actual_entry"),f(x["entry"]));shown_rr=f(x.get("actual_rr"),f(x["rr"]))
    room="\u771f\u5be6\u7d50\u69cb" if x["target_mode"]=="REAL" else "\u50f9\u683c\u63a2\u7d22"
    return ("\U0001f4e1 CryptoRadar V003\uff5c\u5408\u683c\u8a0a\u865f\n"
      f'\u985e\u578b\uff1a{zhfamily(x["family"])}\n\u8a0a\u865f\u4f86\u6e90\uff1a{x.get("venue","Binance")}\n\u5be6\u76e4\u4e0b\u55ae\uff1aBinance\n\u6a19\u7684\uff1a{x["base"]}USDT\n\u65b9\u5411\uff1a{zhside(x["direction"])}\n\u7d50\u69cb\uff1a{zhsetup(x["setup"])}\n\u69fd\u4f4d\uff1a{x["slot"]}\n\n'
      f'\u5be6\u969b\u9032\u5834\uff1a{shown_entry:.10g}\n\u6578\u91cf\uff1a{x.get("quantity",0):.10g}\uff5c\u69d3\u687f\uff1a{x.get("leverage",3)}x\n\u505c\u640d\uff1a{x["stop"]:.10g}\nTP1\uff1a{x["tp1"]:.10g}\uff5c{shown_rr:.2f}R\nTP2\uff1a{x["tp2"]:.10g}\nTP3\uff1a{x["tp3"]:.10g}\n\n'
      f'\u8a72\u5e63\u91cf\u80fd\u500d\u6578\uff1a{x["volume"]:.2f}\n\u56db\u6839K\u6210\u4ea4\u58d3\u529b\uff1a{x["pressure"]:+.3f}\n\u8cc7\u91d1\u8cbb\u7387\uff1a{fr}\nTP1\u7a7a\u9593\uff1a{room}\n'
      "\u51fa\u5834\uff1aTP1\u5168\u90e8\u5e73\u5009\uff1bTP2\u3001TP3\u53ea\u986f\u793a\u53c3\u8003\n\U0001f534 \u5be6\u76e4\u4ea4\u6613\uff1aTP1 \u5168\u90e8\u5e73\u5009\uff0c\u505c\u640d\u7531\u5e63\u5b89\u4ea4\u6613\u6240\u4fdd\u8b77\u3002")

def watch_message(x):
    fr="\u7121\u8cc7\u6599" if x.get("funding") is None else f'{x["funding"]*100:+.4f}%'
    return ("\U0001f50e CryptoRadar V003\uff5c\u4ed6\u6240\u5408\u683c\u8a0a\u865f\n"
      f'\u4f86\u6e90\uff1a{x["venue"]}\n\u6a19\u7684\uff1a{x["base"]}USDT\n\u65b9\u5411\uff1a{zhside(x["direction"])}\n'
      f'\u7d50\u69cb\uff1a{zhsetup(x["setup"])}\n\u9032\u5834\uff1a{x["entry"]:.10g}\n\u505c\u640d\uff1a{x["stop"]:.10g}\n'
      f'TP1\uff1a{x["tp1"]:.10g}\uff5c{x["rr"]:.2f}R\nTP2\uff1a{x["tp2"]:.10g}\nTP3\uff1a{x["tp3"]:.10g}\n'
      f'\u8cc7\u91d1\u8cbb\u7387\uff1a{fr}\n\u53ea\u901a\u77e5\uff0c\u4e0d\u6703\u62ff\u4ed6\u6240\u50f9\u683c\u5230\u5e63\u5b89\u4e0b\u55ae\u3002')

def reentry_watch_message(x):
    return ("\U0001f50e CryptoRadar V003\uff5c\u5047\u505c\u640d\u7ad9\u56de\u8a0a\u865f\n"
      f'\u4f86\u6e90\uff1a{x["venue"]}\n\u6a19\u7684\uff1a{x["base"]}USDT\n\u65b9\u5411\uff1a{zhside(x["direction"])}\n'
      f'\u5efa\u8b70\u9032\u5834\uff1a{x["entry"]:.10g}\n\u5efa\u8b70\u505c\u640d\uff1a{x["stop"]:.10g}\nTP1\uff1a{x["tp1"]:.10g}\uff5c3.00R\n'
      f'\u8a72\u5e63\u91cf\u80fd\u500d\u6578\uff1a{x["volume"]:.2f}\n\u8ddd\u96e2\u539f\u9032\u5834\uff1a{x["extension_atr"]:.2f} ATR\n'
      "\u26a0\ufe0f Binance \u6c92\u6709\u540c\u6a19\u7684\uff0c\u53ea\u901a\u77e5\u4e0d\u4e0b\u55ae\u3002")

def check_reentry_candidates(pool,state):
    """V78: one audited reentry within 12 completed 15m bars after a stop."""
    notices=[];now=time.time();now_bar=pd.Timestamp.now(tz="UTC").floor("15min")
    for old in reversed(state.get("history",[])):
        if str(old.get("exit_reason","")).upper()!="STOP":continue
        stopped=f(old.get("exit_time"))
        if stopped<=0 or not 0<now-stopped<=12*15*60:continue
        key=f'REENTRY_V78:{old.get("base")}:{old.get("direction")}:{int(stopped)}'
        if key in state.get("signals",{}):continue
        venue=str(old.get("venue") or "Binance");source=str(old.get("source_symbol") or old.get("symbol") or "")
        match=next((z for z in pool if z[0]==venue and z[2]==source),None)
        if match is None:match=next((z for z in pool if z[3]==old.get("base")),None)
        if match is None:continue
        venue,scanex,s,base_name=match
        try:
            d=frame(scanex,s);closed=d[d.index<now_bar];stop_dt=pd.to_datetime(stopped,unit="s",utc=True)
            after=closed[closed.index>stop_dt]
            if after.empty:continue
            cur=after.iloc[-1];side=str(old.get("direction","")).upper()
            original=f(old.get("source_entry"),f(old.get("entry")));entry=f(cur.close);atr=f(cur.atr15)
            volume=f(cur.vr_med);trend=(entry>f(cur.ema10)) if side=="LONG" else (entry<f(cur.ema10))
            reclaimed=(entry>original) if side=="LONG" else (entry<original)
            extension=abs(entry-original)/atr if atr>0 else 999.0
            if not (volume>=1.20 and trend and reclaimed and extension<=.35):continue
            if side=="LONG":stop=f(after.low.min())-.10*atr;risk=entry-stop;tp1=entry+3*risk
            else:stop=f(after.high.max())+.10*atr;risk=stop-entry;tp1=entry-3*risk
            if risk<=0 or min(entry,stop,tp1)<=0:continue
            tp2=entry+(5*risk if side=="LONG" else -5*risk);tp3=entry+(7*risk if side=="LONG" else -7*risk)
            notices.append(dict(venue=venue,source_symbol=s,symbol=s,base=base_name,direction=side,
              family="EXPANSION",setup="SAFE_REENTRY",signal_time=cur.name.isoformat(),entry=entry,stop=stop,
              tp1=tp1,tp2=tp2,tp3=tp3,rr=3.0,volume=volume,pressure=f(cur.pressure),priority=90.0,
              v61_priority=90.0,v70_priority=90.0,elite_ok=True,flex_ok=True,
              target_mode="V78_SAFE_REENTRY",extension_atr=extension,reentry_key=key,live_rule="V78_SAFE_REENTRY"))
        except Exception as e:log.debug("reentry candidate %s failed: %s",old.get("base"),e)
    return notices

def monitor(ex,state):
    return TRADER.reconcile(state,tg,zhside,save_state)
def allocate(cands,state,external_bases=None,external_count=0):
    active=list(state["positions"].values());core_n=sum(x.get("slot")=="\u6838\u5fc3\u4fdd\u8b77\u5009" for x in active)
    flex_n=sum(x.get("slot") in {"\u64f4\u5f35\u5009","\u6838\u5fc3\u501f\u7528\u64f4\u5f35\u5009"} for x in active)
    active_dirs={d:sum(str(x.get("direction")).upper()==d for x in active) for d in ("LONG","SHORT")}
    cutoff=time.time()-6*3600
    recent=list(state.get("history",[]))+active
    recent_dirs={d:sum(str(x.get("direction")).upper()==d and f(x.get("open_time"))>=cutoff for x in recent) for d in ("LONG","SHORT")}
    cands=v003live.finalize_cycle(cands,max(0,CORE_SLOTS-core_n),active_dirs,recent_dirs)
    bases=set(external_bases or ())|{x["base"] for x in active};chosen=[]
    cands.sort(key=lambda x:(0 if x["family"]=="CORE" else 1,-x["priority"],x["symbol"]))
    for x in cands:
        if external_count+len(chosen)>=MAX_POSITIONS:break
        if x["base"] in bases:continue
        if x["family"]=="CORE":
            if core_n<CORE_SLOTS:x["slot"]="\u6838\u5fc3\u4fdd\u8b77\u5009";core_n+=1
            elif flex_n<FLEX_SLOTS:x["slot"]="\u6838\u5fc3\u501f\u7528\u64f4\u5f35\u5009";flex_n+=1
            else:continue
        else:
            if flex_n>=FLEX_SLOTS:continue
            x["slot"]="\u64f4\u5f35\u5009";flex_n+=1
        bases.add(x["base"]);chosen.append(x)
    return chosen

def main():
    global TRADER
    if SHADOW and os.getenv("STATE_FILE") is None:
        global STATE
        STATE=ROOT/"radar_v001_shadow_state.json"
    lock_handle=(ROOT/("radar_v001_shadow.lock" if SHADOW else "radar_v001.lock")).open("w")
    try:fcntl.flock(lock_handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError("CryptoRadar is already running")
    ex=client();scanners=scan_clients(ex);trade_symbols=binance_trade_map(ex);state=load_state();TRADER=LiveTrader(ex,ROOT);account=TRADER.account() if SHADOW else TRADER.guard();stop=False;pool=[]
    def halt(*_):
        nonlocal stop;stop=True
    signal.signal(signal.SIGINT,halt);signal.signal(signal.SIGTERM,halt)
    snap=TRADER.snapshot()
    startup=(f"\U0001f4e1 CryptoRadar V003 \u5be6\u76e4\u5df2\u555f\u52d5\n"
       f"\u6383\u63cf\uff1aBinance\u3001Bitget\u3001BingX\n\u5be6\u76e4\uff1a\u5e63\u5b89 USDT \u6c38\u7e8c\u5408\u7d04\n"
       f"\u7b56\u7565\uff1aV72.8S\uff0bV78\uff5c\u6838\u5fc3\u6700\u4f4e4R\uff0b\u5f37\u91cf3R\u7a7a\u55ae\uff0b\u5047\u505c\u640d\u5b89\u5168\u91cd\u9032\uff0bTop5\n"
       f"\u98a8\u63a7\uff1a\u5168\u5009 3 \u500d\uff5c\u55ae\u7b46 1%\uff5c\u6700\u591a 5 \u5009\uff5c\u7576\u65e5 -2% \u505c\u6b62\u958b\u5009\n"
       f"\u5df2\u6709\u6301\u5009\uff1a{snap['count']} \u5009\uff08V003 \u4e0d\u63a5\u7ba1\uff09\n"
       "\u51fa\u5834\uff1a\u4ea4\u6613\u6240\u505c\u640d\uff0cTP1 \u5168\u90e8\u5e73\u5009\uff1bTP2/TP3 \u53c3\u8003")
    if SHADOW:log.info("SHADOW MODE: no orders and no Telegram startup message")
    else:tg(startup)
    while not stop:
        started=time.time()
        try:
            if not pool:
                targets,discovery_errors=v003live.discover_targets(scanners)
                pool=[(t.exchange_name,t.exchange,t.symbol,t.base) for t in targets]
                # V22 knows that e.g. an exchange contract is an MU/MSFT/IBM
                # token.  Preserve that underlying mapping for Binance execution
                # instead of guessing from the contract prefix.
                for t in targets:
                    if t.exchange_name=="Binance":trade_symbols.setdefault(str(t.base).upper(),t.symbol)
                counts={name:sum(t.exchange_name==name for t in targets) for name,_ in scanners}
                log.info("V003 merged market pool %d counts=%s discovery_errors=%d",len(pool),counts,len(discovery_errors))
            if not SHADOW:monitor(ex,state)
            snap=TRADER.snapshot()
            active={x["base"] for x in state["positions"].values()}|set(snap["bases"]);cands=[];watch_best={};scan_error_count=0;scan_error_samples=[]
            for n,item in enumerate(pool,1):
                venue,scanex,s,base_name=item
                if stop:break
                if base_name in active:continue
                try:
                    x=analyze(scanex,s,venue,base_name)
                    if x:
                        x["venue"]=venue;x["source_symbol"]=s
                        # Only the audited V72.8S elite short-flex path may trade at 3R-4R.
                        if 3.0<=f(x.get("rr"))<4.0:
                            if not (x.get("family")=="EXPANSION" and x.get("flex_ok") and x.get("flex_reject_reason")=="V72.8S 3R\u7a7a\u55ae"):
                                continue
                            x["live_rule"]="V72_8S_RR3_SHORT"
                        trade_symbol=trade_symbols.get(x["base"])
                        if venue=="Binance" or trade_symbol:
                            if venue!="Binance":x=translate_to_binance(x,trade_symbol,ex)
                            k=f'{x["base"]}:{x["direction"]}:{x["family"]}'
                            if time.time()-f(state["signals"].get(k))>=COOLDOWN:cands.append(x)
                        else:
                            wk=f'WATCH:{x["base"]}:{x["direction"]}'
                            if time.time()-f(state["signals"].get(wk))>=COOLDOWN:
                                old=watch_best.get((x["base"],x["direction"]))
                                if old is None or x["priority"]>old["priority"]:watch_best[(x["base"],x["direction"])]=x
                except Exception as e:
                    scan_error_count+=1
                    if len(scan_error_samples)<50:scan_error_samples.append({"venue":venue,"symbol":s,"error":f"{type(e).__name__}: {e}"})
                    log.debug("%s %s",s,e)
                if n%10==0 or n==len(pool):
                    print(f"\r三大交易所掃描中  {n}/{len(pool)}",end="",flush=True)
            print(flush=True)
            for x in check_reentry_candidates(pool,state):
                if x["base"] in active:continue
                trade_symbol=trade_symbols.get(x["base"])
                if x["venue"]!="Binance" and trade_symbol:x=translate_to_binance(x,trade_symbol,ex)
                if x["venue"]=="Binance" or trade_symbol:
                    cands.append(x)
                else:
                    state["signals"][x["reentry_key"]]=time.time();save_state(state);tg(reentry_watch_message(x))
            binance_keys={(x["base"],x["direction"]) for x in cands}
            for wk,x in watch_best.items():
                if wk in binance_keys:continue
                x["funding"]=funding(next(e for v,e in scanners if v==x["venue"]),x["source_symbol"])
                state["signals"][f'WATCH:{x["base"]}:{x["direction"]}']=time.time();save_state(state);tg(watch_message(x))
                log.info("watch accepted %s %s %s",x["venue"],x["base"],x["direction"])
            picked=allocate(cands,state,snap["bases"],snap["count"])
            if SHADOW:
                audit={"time":pd.Timestamp.now(tz="UTC").isoformat(),"market_count":len(pool),"raw_candidates":len(cands),"selected":picked,
                       "scan_error_count":scan_error_count,"scan_error_samples":scan_error_samples}
                (ROOT/"v003_shadow_result.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
                log.info("SHADOW complete markets=%d candidates=%d selected=%d",len(pool),len(cands),len(picked))
                if RUN_ONCE:break
                picked=[]
            for x in picked:
                x["funding"]=funding(ex,x["symbol"]);x["status"]="pending";x["open_time"]=time.time()
                k=f'{x["base"]}:{x["direction"]}:{x["family"]}'
                state["pending"]={"key":k,"symbol":x["symbol"],"time":time.time()};save_state(state)
                try:
                    x.update(TRADER.enter(x,state));x["status"]="open"
                    state["signals"][k]=time.time()
                    if x.get("reentry_key"):state["signals"][x["reentry_key"]]=time.time()
                    state["positions"][k]=x;state.pop("pending",None)
                    save_state(state);tg(message(x));log.info("LIVE accepted %s %s %s qty=%s",x["base"],x["family"],x["direction"],x.get("quantity"))
                except Exception as e:
                    state.pop("pending",None);save_state(state)
                    log.warning("LIVE rejected %s: %s",x["base"],e)
                    tg(f'\u26a0\ufe0f V003 \u672a\u9032\u5834\n\u6a19\u7684\uff1a{x["base"]}USDT\n\u539f\u56e0\uff1a{e}')
            if not picked:log.info("cycle complete no new signal")
        except Exception:log.exception("cycle failed")
        remain=max(0,SCAN-(time.time()-started))
        while remain>0 and not stop:
            z=min(1,remain);time.sleep(z);remain-=z
    return 0
if __name__=="__main__":raise SystemExit(main())
