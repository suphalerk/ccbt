---
name: Gold Ichimoku Strategy Analysis
description: Deep analysis of 3 PM questions on gold Ichimoku — long-only wins decisively, Chikou improves both-direction PF by 17%, pure trail beats hybrid on gold
type: project
---

Gold Ichimoku strategy analysis on 2.4yr XAU/USD 1H data (13,691 bars, $1999->$4720).

**Key findings:**

1. **Long-only is the clear winner**: PF 1.90 vs 1.34 (both), DD 15.7% vs 39.3%. Shorts have PF 0.48-0.64 across ALL quarters and ALL configs. Not a single quarter where shorts are consistently profitable.

2. **Chikou Span confirmation is valuable for both-direction mode**: filters 18% of signals, improves PF from 1.34->1.57 (+17%), reduces DD from 39.3%->30.8%. But for long-only mode, Chikou adds marginal value (PF 1.90->2.00) while reducing trade count from 132->118.

3. **Pure trailing stop beats hybrid partial-close on gold**: Pure trail PF 1.34-1.50 vs hybrid 30% PF 1.19-1.28 (both dir). Gold trends run far — partial close locks in profit but cap the runners that make gold trend-following profitable.

**Recommended config**: Long-only, SL 3.0, Trail 3.5 (PF 2.01, 56%/yr, DD 12.1%)

**Why:** line — gold has a structural uptrend bias and shorts actively destroy capital
**How to apply:** — implement long-only mode as default for gold; add Chikou only if both-direction mode is ever needed as a safety filter
