# Portfolio Backtest Methodology (Corrected)

> ⚠️ Read this before writing or trusting any portfolio backtest. The R-multiple bug here inflated past results 10x. See also the audit checklist below — mandatory before deploying any new strategy.

Shared wallet backtest must follow these rules to avoid inflated results:

## R-Multiple Method (correct)
```python
# For each bot: track engine balance separately
engine_balance = 10000.0
for trade in bot_trades:
    engine_risk = engine_balance * risk_per_trade  # what engine risked
    r_multiple = trade.pnl / engine_risk           # actual R won/lost
    engine_balance += trade.pnl                    # engine compounds

# Shared wallet replay:
shared_risk = shared_balance * risk_per_trade      # 1% of shared balance
dollar_pnl = shared_risk * r_multiple              # apply R to shared wallet
shared_balance += dollar_pnl
```

## Common Bugs to Avoid
- **Double compounding**: NEVER do `pnl_frac = pnl / initial_balance` then `balance * pnl_frac` — this inflates 30-100% because engine compounds internally
- **R-multiple risk mismatch**: When computing R-multiple, use ACTUAL engine risk (`config['risk_per_trade']`), not fixed 1%. BTC uses 5% risk → PnL 5x larger → if you divide by 1% you get R 5x too high. Fix: `r = pnl / (engine_bal × actual_risk)`, shared wallet always applies 1%
- **No concurrent limit**: Must cap simultaneous open positions — Main setting: max 10. At max 5, you skip 65% of trades
- **Loose filters**: Require data >= 12 months AND trades >= 10 per bot — otherwise statistically meaningless

## Validation Filters
- **Data minimum**: 12 months of hourly data (coin must have been listed >= 1yr)
- **Trade minimum**: 10 trades in backtest period (statistical significance)
- **Concurrent limit**: Max 5 open positions at any time
- **Data snooping**: Be aware that selecting best from 8,700+ backtests inflates results

## Audit Checklist (before deploying any new strategy)
1. **Signal verification** — print every trade, verify indicator values at entry match conditions
2. **Look-ahead check** — signal uses iloc[-2] (closed candle), entry at iloc[-1] close
3. **Fee check** — verify PnL includes commission + slippage correctly
4. **Walk-forward test** — split data in half, run each separately. OOS PF should be >= 60% of IS PF
5. **Trade count** — minimum 15 trades for deployment. Under 15 = high variance / luck
6. **Baseline comparison** — compare vs current strategy on SAME data period
- If walk-forward degrades > 40% OR trades < 15: **REJECT** regardless of PF
- Script template: `research/audit_btc_wif.py`
