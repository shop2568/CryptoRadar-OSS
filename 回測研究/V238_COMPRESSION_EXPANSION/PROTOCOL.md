# V238 observation definitions (before outcomes)

Research only2023/2024. No HTF. Frozen V18816/9 fixed semantics, unchanged.
Use transformed immutable signal_time as signal candle open, require +15m<=entry; no entry-15m fallback. If a delayed entry retains an earlier signal, features describe that original signal, not a fabricated later breakout.
38 continuous15m bars through signal, previous37 available. Missing strict. ATR20 prior20 TR simple mean. Compression uses prior8 medianTR/ATR20 (<.5 strong,<1 mild, else ATR5/20>1 expanding, else none). Prior16/8 realized-range and4std(close)/ATR diagnostic only. No empirical threshold selection.
Volume last3/6 vs disjoint20bars ending before last6; extra30reference diagnostic. Healthy bothratios>1 and>=2 consecutive rises. Exhausted ratio6>=2 andlast3<previous3; single spike ratio3<=1 and signalvolume>=2prior20median; otherflat.
Quality sign mirrored: adversewick>=.5 or CLV<=.25 rejection; else TR/priorATR>=3 oversized; else signedbody>=.5,CLV>=.75,TR/ATR>=1 andclose beyondprior16high/low clean; elseweak. The prior16 boundary is an explicit observational structure proxy, NOT claimed to be each module's original trigger price.
Transition priority rejection > unbuiltspike > nobase > healthy > weak. Unbuiltspike nohealthybuild plusvolume>=2orTR>=3. Healthy requirescompression+build+clean; weak remainingcompressed, including clean-without-build. No score or grid search.
Stability: years separate, LONG/SHORT separate, top1/3 win/loss removals, symbol concentration, exchange coverage, loserrejection minus TP1winnersacrifice. Descriptive sample diagnostics not significance. Cross-exchange stability cannot be proven by single-venue samples. No variants unless stable evidence.
