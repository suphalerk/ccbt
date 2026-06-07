# Backend Dev Memory Index

## User
- [user_role.md](user_role.md) — User role and collaboration style

## Project
- [project_known_bugs.md](project_known_bugs.md) — Critical bugs identified in code review (2026-03-18)
- [project_5m_research_2026_03_20.md](project_5m_research_2026_03_20.md) — 5m BTC EMA crossover: all 14 configs fail (PF < 1.0), strategy edge does not exist on 5m
- [project_multi_coin_research_2026_03_20.md](project_multi_coin_research_2026_03_20.md) — Multi-coin 15m test: edge exists on BTC only, ETH/SOL/PAXG all PF < 1.10
- [project_altcoin_mega_sweep_2026_03_20.md](project_altcoin_mega_sweep_2026_03_20.md) — 150-combo sweep: Ichimoku 1h only positive strategy; PA fails everywhere; 15m confirmed dead

- [project_ichimoku_1h_verified_2026_03_21.md](project_ichimoku_1h_verified_2026_03_21.md) — Ichimoku 1H full-engine verification: AVAX/NEAR/SOL confirmed profitable; ARB/XRP/WIF fail full engine despite passing lightweight sim

- [project_new_strats_sweep_2026_03_21.md](project_new_strats_sweep_2026_03_21.md) — S11-S20 sweep: 1738 combos, top picks: VVV EMA+Ichi 4H (PF 5.12), ATOM VolExp+ST (PF 5.38), 1000PEPE VolExp+ST (PF 3.62), IP London Open (PF 2.78)
- [project_sweep_new_strats_2026_03_21.md](project_sweep_new_strats_2026_03_21.md) — 10 new strategies (ADX/StochRSI/CCI/Alligator/PSAR/Elder/Aroon/DualST/IchiST/RSI50) across 133 coins: 4H consistently better; top candidates XRP/LINK/FIL DualST 4H, DOT/FET/NEAR high-trade-count strategies

- [project_round9_verify_2026_03_21.md](project_round9_verify_2026_03_21.md) — Round 9: 14/77 new coins pass engine verify; 61-bot portfolio $200→$3,131 (+1466%)
- [project_round10_2026_03_22.md](project_round10_2026_03_22.md) — Round 10: 3 GitHub strategies implemented (DualThrust/AO/RangeBounce); 76/180 pass engine; portfolio $200→$1,106 (+453%)
- [project_round11_2026_03_22.md](project_round11_2026_03_22.md) — Round 11: 3 new strategies (StochMTF/ZScoreMeanRev/EMAribbon); 36/87 pass engine; portfolio $200→$1,579 (+689%)
- [project_round12_combo_verify_2026_03_22.md](project_round12_combo_verify_2026_03_22.md) — Round 12: 5 combo signals verified; 23/120 pass; DualThrust+ADX dominant (13 wins); $200→$410 (+105%) for 27-bot subset

- [project_full_portfolio_74_2026_03_22.md](project_full_portfolio_74_2026_03_22.md) — Full 147-bot combined portfolio: $200→$1,789.58 (+794.8%), 1541 trades, 23% max DD
- [project_core7_new_strats_2026_03_22.md](project_core7_new_strats_2026_03_22.md) — Core 7 coins × 32 new strategies (224 backtests): 4 upgrade candidates found (AVAX/BTC/WIF/ARC)
- [project_audit_btc_wif_2026_03_22.md](project_audit_btc_wif_2026_03_22.md) — Audit: BTC Stoch MTF 1H and WIF DualThrust+ADX 1H both REJECTED — PF numbers real but too few trades, walk-forward unstable

- [project_sweep_improve_weak_2026_03_22.md](project_sweep_improve_weak_2026_03_22.md) — 30 weak coins sweep: 682/5940 passing; zscore_meanrev 1H dominates (VVV PF 6.17, CRV 5.82); needs BacktestEngine verify
- [project_verify_weak_2026_03_22.md](project_verify_weak_2026_03_22.md) — Weak coin engine verify (29 coins): 8/29 pass; top picks PENGU 4H Ichi PF 6.0, POL 4H Ichi PF 3.6, AVAX EMA ribbon 4H PF 2.1, ALICE AO 4H PF 2.0
- [project_final_backtest_2026_03_22.md](project_final_backtest_2026_03_22.md) — Final portfolio with 6 weak-coin upgrades: $200→$3,421 (+1610%), 12.4% max DD (vs 23% before)
- [project_round13_combined_2026_03_23.md](project_round13_combined_2026_03_23.md) — R13: 125+11 bots combined; $200→$1,449 (+624%), DD 12.9%; BAN/1000PEPE dualthrust rejected (PF<1.1)

- [project_dashboard_round1_fixes_2026_06_07.md](project_dashboard_round1_fixes_2026_06_07.md) — Round 1: 4 blockers fixed (roster from start.sh, gate since=, lifespan safety, WS token); 292 tests pass
- [project_dashboard_round2_nits_2026_06_07.md](project_dashboard_round2_nits_2026_06_07.md) — Round 2: segment-exact SPA guard, Cache-Control no-store, WS snapshot parity tests (13 tests)

## Feedback
- [feedback_review_style.md](feedback_review_style.md) — How thorough code reviews should be structured
- [feedback_dashboard_parity_testing.md](feedback_dashboard_parity_testing.md) — queries.py semantics: orphan exclusion scope, win_rate already %, daily_pnl column, SPA %2F routing gap
