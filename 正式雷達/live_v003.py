#!/usr/bin/env python3
"""CryptoRadar V003 live execution safety layer for Binance USD-M futures."""
from __future__ import annotations
import math,time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.02
MAX_POSITIONS=5
LEVERAGE=3
PER_POSITION_NOTIONAL=0.60
CHASE_R=0.10
TZ=ZoneInfo("Asia/Taipei")
RR3_LIVE_RULES={"V72_8S_RR3_SHORT","V78_SAFE_REENTRY"}

def num(x,default=0.0):
    try:
        z=float(x)
        return z if math.isfinite(z) else default
    except (TypeError,ValueError):
        return default

def load_env(root:Path,name:str):
    out={}
    p=root/name
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
                k,v=raw.split("=",1);out[k.strip()]=v.strip()
    return out

class LiveTrader:
    def __init__(self,ex,root:Path):
        self.ex=ex
        self.root=root
        self.last_emergency_reason=""

    def guard(self):
        flag=self.root/"LIVE_TRADING_ENABLED"
        if not flag.exists() or flag.read_text(encoding="utf-8").strip()!="I_ACCEPT_LIVE_TRADING":
            raise RuntimeError("live trading flag missing")
        b=self.ex.fetch_balance()
        info=b.get("info",{})
        eq=num(info.get("totalMarginBalance")) or num(info.get("totalWalletBalance"))
        av=num(info.get("availableBalance"))
        if eq<=0 or av<=0:
            raise RuntimeError("no available USDT futures balance")
        mode=self.ex.fapiPrivateGetPositionSideDual()
        if str(mode.get("dualSidePosition")).lower()=="true":
            raise RuntimeError("hedge mode is not supported; use one-way mode")
        return {"equity":eq,"available":av}

    def snapshot(self):
        positions=[]
        for p in self.ex.fetch_positions():
            amt=abs(num(p.get("contracts")))
            if amt<=0: continue
            positions.append(p)
        return {
            "count":len(positions),
            "bases":{str(p.get("symbol","")).split("/")[0].replace("1000","") for p in positions},
            "symbols":{str(p.get("symbol","")) for p in positions},
            "positions":positions,
        }

    def account(self):
        b=self.ex.fetch_balance()
        i=b.get("info",{})
        return {
            "equity":num(i.get("totalMarginBalance")) or num(i.get("totalWalletBalance")),
            "wallet":num(i.get("totalWalletBalance")),
            "available":num(i.get("availableBalance")),
        }

    def daily_allowed(self,state):
        a=self.account();day=datetime.now(TZ).date().isoformat()
        d=state.setdefault("daily",{})
        if d.get("date")!=day or num(d.get("start_equity"))<=0:
            d.clear();d.update(date=day,start_equity=a["equity"],locked=False,notified=False)
        start=num(d.get("start_equity"))
        loss=(start-a["equity"])/start if start>0 else 0
        if loss>=MAX_DAILY_LOSS:
            d["locked"]=True
        return (not d.get("locked")),loss,a

    def market_info(self,symbol):
        m=self.ex.market(symbol)
        return m,m["id"]

    def _position(self,symbol):
        for p in self.ex.fetch_positions([symbol]):
            if p.get("symbol")==symbol and abs(num(p.get("contracts")))>0:
                return p
        return None

    def _set_risk_mode(self,symbol):
        m,sid=self.market_info(symbol)
        try:
            self.ex.fapiPrivatePostMarginType({"symbol":sid,"marginType":"CROSSED"})
        except Exception as e:
            if "-4046" not in str(e):
                raise
        self.ex.fapiPrivatePostLeverage({"symbol":sid,"leverage":LEVERAGE})
        return m,sid

    def _live_price(self,x):
        t=self.ex.fetch_ticker(x["symbol"])
        if x["direction"]=="LONG":
            return num(t.get("ask")) or num(t.get("last"))
        return num(t.get("bid")) or num(t.get("last"))

    def _minimum_live_rr(self,x):
        """Only audited V72.8S/V78 paths may trade below 4R."""
        return 3.0 if str(x.get("live_rule") or "") in RR3_LIVE_RULES else 4.0

    def _size(self,x,price,state):
        ok,loss,a=self.daily_allowed(state)
        if not ok:
            raise RuntimeError(f"daily loss lock {loss:.2%}")
        stop=num(x["stop"]);tp1=num(x["tp1"]);planned=num(x["entry"])
        risk=price-stop if x["direction"]=="LONG" else stop-price
        if min(price,risk)<=0:
            raise RuntimeError("invalid live risk")
        if x["direction"]=="LONG":
            if price>planned+CHASE_R*abs(planned-stop):
                raise RuntimeError("price chased above allowed range")
            rr=(tp1-price)/risk
        else:
            if price<planned-CHASE_R*abs(planned-stop):
                raise RuntimeError("price chased below allowed range")
            rr=(price-tp1)/risk
        minimum_rr=self._minimum_live_rr(x)
        if rr<minimum_rr:
            raise RuntimeError(f"live RR below {minimum_rr:.0f}R ({rr:.2f})")
        risk_qty=a["equity"]*RISK_PER_TRADE/risk
        cap_notional=min(a["equity"]*PER_POSITION_NOTIONAL,a["available"]*LEVERAGE*0.90)
        qty=min(risk_qty,cap_notional/price)
        qty=num(self.ex.amount_to_precision(x["symbol"],qty))
        m=self.ex.market(x["symbol"]);lim=m.get("limits",{})
        min_amt=num((lim.get("amount") or {}).get("min"))
        min_cost=max(5.0,num((lim.get("cost") or {}).get("min")))
        if qty<=0 or qty<min_amt or qty*price<min_cost:
            raise RuntimeError("position below exchange minimum")
        return qty,rr,a,loss

    def _client_id(self,prefix):
        return ("V003"+prefix+str(int(time.time()*1000)))[-32:]

    def _algo(self,sid,side,kind,trigger,client_id):
        return self.ex.fapiPrivatePostAlgoOrder({
            "algoType":"CONDITIONAL",
            "symbol":sid,
            "side":side,
            "positionSide":"BOTH",
            "type":kind,
            "triggerPrice":trigger,
            "closePosition":"TRUE",
            "workingType":"MARK_PRICE",
            "priceProtect":"FALSE",
            "clientAlgoId":client_id,
            "newOrderRespType":"ACK",
        })

    def _cancel_algo(self,sid,algo_id):
        if not algo_id:return
        try:self.ex.fapiPrivateDeleteAlgoOrder({"symbol":sid,"algoId":algo_id})
        except Exception:pass

    def _close_market(self,symbol,qty,reason):
        self.last_emergency_reason=reason
        _,sid=self.market_info(symbol)
        side="SELL" if num(qty)>0 else "BUY"
        amount=abs(num(qty))
        if amount<=0:
            p=self._position(symbol)
            if not p:return None
            signed=num((p.get("info") or {}).get("positionAmt"))
            if signed==0:
                signed=amount if str(p.get("side")).lower()=="long" else -amount
            side="SELL" if signed>0 else "BUY";amount=abs(signed)
        amount=self.ex.amount_to_precision(symbol,amount)
        return self.ex.fapiPrivatePostOrder({
            "symbol":sid,"side":side,"type":"MARKET","quantity":amount,
            "reduceOnly":"true","newClientOrderId":self._client_id("EMG"),
            "newOrderRespType":"RESULT",
        })

    def enter(self,x,state):
        flag=self.root/"LIVE_TRADING_ENABLED"
        if not flag.exists() or flag.read_text(encoding="utf-8").strip()!="I_ACCEPT_LIVE_TRADING":
            raise RuntimeError("live trading kill switch is off")
        snap=self.snapshot()
        if snap["count"]>=MAX_POSITIONS:
            raise RuntimeError("maximum five positions reached")
        if x["symbol"] in snap["symbols"] or x["base"] in snap["bases"]:
            raise RuntimeError("same asset already has a position")
        price=self._live_price(x)
        qty,live_rr,a,day_loss=self._size(x,price,state)
        m,sid=self._set_risk_mode(x["symbol"])
        side="BUY" if x["direction"]=="LONG" else "SELL"
        order=self.ex.fapiPrivatePostOrder({
            "symbol":sid,"side":side,"type":"MARKET","quantity":self.ex.amount_to_precision(x["symbol"],qty),
            "newClientOrderId":self._client_id("ENT"),"newOrderRespType":"RESULT",
        })
        executed=num(order.get("executedQty")) or qty
        avg=num(order.get("avgPrice"))
        time.sleep(0.8)
        pos=self._position(x["symbol"])
        if pos:
            avg=num(pos.get("entryPrice")) or avg or price
            signed=num((pos.get("info") or {}).get("positionAmt"))
        else:
            signed=executed if x["direction"]=="LONG" else -executed
        actual_risk=avg-num(x["stop"]) if x["direction"]=="LONG" else num(x["stop"])-avg
        actual_rr=((num(x["tp1"])-avg)/actual_risk if x["direction"]=="LONG" else (avg-num(x["tp1"]))/actual_risk) if actual_risk>0 else 0
        minimum_rr=self._minimum_live_rr(x)
        if actual_rr<minimum_rr:
            self._close_market(x["symbol"],signed,f"actual RR below {minimum_rr:.0f}R ({actual_rr:.2f})")
            raise RuntimeError(f"actual fill RR below {minimum_rr:.0f}R ({actual_rr:.2f}); position closed")
        stop=self.ex.price_to_precision(x["symbol"],num(x["stop"]))
        tp1=self.ex.price_to_precision(x["symbol"],num(x["tp1"]))
        exit_side="SELL" if x["direction"]=="LONG" else "BUY"
        stop_order=None;tp_order=None
        try:
            stop_order=self._algo(sid,exit_side,"STOP_MARKET",stop,self._client_id("SL"))
            tp_order=self._algo(sid,exit_side,"TAKE_PROFIT_MARKET",tp1,self._client_id("TP"))
        except Exception as e:
            if stop_order:self._cancel_algo(sid,stop_order.get("algoId"))
            if tp_order:self._cancel_algo(sid,tp_order.get("algoId"))
            self._close_market(x["symbol"],signed,f"protection failed: {e}")
            raise RuntimeError("protection order failed; position closed") from e
        return {
            "live":True,"entry_order_id":order.get("orderId"),"quantity":executed,
            "actual_entry":avg,"actual_rr":actual_rr,"leverage":LEVERAGE,
            "risk_usdt":a["equity"]*RISK_PER_TRADE,
            "stop_algo_id":stop_order.get("algoId"),"tp_algo_id":tp_order.get("algoId"),
            "exchange_symbol":sid,"day_loss_at_entry":day_loss,
        }

    def _algo_status(self,sid,algo_id):
        if not algo_id:return {}
        try:return self.ex.fapiPrivateGetAlgoOrder({"symbol":sid,"algoId":algo_id})
        except Exception:return {}

    def _active_algo(self,z):
        return str(z.get("algoStatus") or z.get("status") or "").upper() in {"NEW","WORKING","PARTIALLY_FILLED"}

    def reconcile(self,state,tg,zhside,save_state):
        changed=False
        for k,x in list(state.get("positions",{}).items()):
            if not x.get("live"):continue
            try:
                p=self._position(x["symbol"])
                sid=x.get("exchange_symbol") or self.ex.market(x["symbol"])["id"]
                sl=self._algo_status(sid,x.get("stop_algo_id"))
                tp=self._algo_status(sid,x.get("tp_algo_id"))
                if p:
                    if not self._active_algo(sl):
                        signed=num((p.get("info") or {}).get("positionAmt"))
                        self._cancel_algo(sid,x.get("tp_algo_id"))
                        self._close_market(x["symbol"],signed,"stop protection missing")
                        tg(f'\u26d4 V003\u7dca\u6025\u5e73\u5009\\n\u6a19\u7684\uff1a{x["base"]}USDT\\n\u539f\u56e0\uff1a\u4ea4\u6613\u6240\u505c\u640d\u4fdd\u8b77\u5931\u6548')
                    continue
                self._cancel_algo(sid,x.get("stop_algo_id"));self._cancel_algo(sid,x.get("tp_algo_id"))
                sls=str(sl.get("algoStatus") or sl.get("status") or "").upper()
                tps=str(tp.get("algoStatus") or tp.get("status") or "").upper()
                reason="TP1" if tps in {"FINISHED","FILLED","TRIGGERED"} else ("STOP" if sls in {"FINISHED","FILLED","TRIGGERED"} else "CLOSED")
                x.update(status="closed",exit_reason=reason,exit_time=time.time())
                state.setdefault("history",[]).append(x);del state["positions"][k];changed=True
                label="\u2705 TP1\u5168\u90e8\u5e73\u5009" if reason=="TP1" else ("\u274c \u505c\u640d\u5e73\u5009" if reason=="STOP" else "\u26a0\ufe0f \u6301\u5009\u5df2\u7d50\u675f")
                tg(f'\U0001f4cc V003\uff5c\u5be6\u76e4\u4ea4\u6613\u7d50\u675f\\n\u6a19\u7684\uff1a{x["base"]}USDT\\n\u65b9\u5411\uff1a{zhside(x["direction"])}\\n\u7d50\u679c\uff1a{label}')
            except Exception as e:
                import logging;logging.getLogger("V003").exception("live reconcile %s failed",k)
        ok,loss,a=self.daily_allowed(state)
        d=state.setdefault("daily",{})
        if not ok and not d.get("notified"):
            d["notified"]=True;changed=True
            tg(f'\u26d4 V003\u5df2\u505c\u6b62\u958b\u65b0\u5009\\n\u539f\u56e0\uff1a\u7576\u65e5\u6b0a\u76ca\u56de\u843d {loss:.2%}\uff0c\u9054\u5230 2% \u98a8\u63a7\u7dda\u3002')
        if changed:save_state(state)
        return changed
