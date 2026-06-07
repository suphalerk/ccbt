---
name: project-dashboard-round2-frontend-fix
description: Round 2 frontend fix — config_count rendering in TradeGateTable
metadata:
  type: project
---

Round 2 frontend fix committed 2026-06-07 on branch `dashboard-rewrite-impl`.

**BUG 2 (config_count not rendered in UI):** `TradeGateTable` received `config_count`
from the server in the `TradeGateRow` schema but never rendered it. The backend fix
(committed earlier this round) made `get_trade_gate()` emit `config_count` in `rows_out`
so the API no longer always returns `config_count=1`. The frontend fix displays
`N cfgs` inline in the Symbol cell when `config_count > 1`.

**TDD discipline:** Added a failing test first (`renders config_count in the row for
MIXED symbol`) that asserts `row.textContent` matches `/3 cfg|3 config|cfgs?: 3/i`.
The test confirmed the bug (DOM showed nothing about config_count) before the fix.

**Why tests were masking the bug:** Prior tests had `config_count: 4` in mock data
but never asserted the count appeared in the DOM. The gap between "what the server
sends" and "what the user sees" was invisible to CI.

**How to apply:** When adding columns from server data to a table component, always
add a DOM assertion in the test — not just that the row renders, but that the specific
field's value appears in `row.textContent` or a named `data-testid`.
