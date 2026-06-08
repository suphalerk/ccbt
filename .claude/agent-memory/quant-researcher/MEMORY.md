# Quant Researcher Memory Index

## Project
- [backtest-engine-audit-2026-06-08.md](project_backtest_engine_audit_2026_06_08.md) — soundness review: strong look-ahead defense + correct R-multiple, BUT funding not charged, taker fees under-modeled, data-snooping uncorrected, profile returns inflated by leverage not alpha
- [backtest-tests-pra-review-2026-06-08.md](project_backtest_tests_pra_review_2026_06_08.md) — PR-A char suite mostly mutation-verified sound; PIN-4 regime pin TAUTOLOGICAL, partial-TP cash-conservation unpinned, parity regex reads to EOF
- [backtest-tests-pra-round1-2026-06-08.md](project_backtest_tests_pra_round1_2026_06_08.md) — round-1 follow-ups: all 5 points mutation-confirmed (PIN-4/loss-snap/cash-conserve/parity-slice/diff); phantom flakiness was self-inflicted concurrent pytest; backtest parity slice still EOF-unbounded (mitigated by count pins)
