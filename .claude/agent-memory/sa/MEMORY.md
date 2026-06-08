# Solution Architect — Memory Index

## Project
- [Dashboard rewrite ADR + adherence gaps](project_dashboard_rewrite_adr.md) — api/+web/ split; Python is single source of truth (zero TS math); known gaps to fix before cutover
- [Multi-bot close dedup](project_multibot_close_dedup.md) — shared recently_closed registry (lock-free, atomic under asyncio); deferred mark; exit-ratio guard; known netting attribution limit
