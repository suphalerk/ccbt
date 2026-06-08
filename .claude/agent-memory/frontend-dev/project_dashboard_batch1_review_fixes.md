---
name: project_dashboard_batch1_review_fixes
description: Batch 1 code-review fixes: mode casing BLOCKER, dead error banner MAJOR, a11y confirm dialogs MAJOR
metadata:
  type: project
---

Applied confirmed review fixes to the Batch 1 operational controls.

**BLOCKER — mode case mismatch fixed in two layers:**
- `api/routers/portfolio.py` now uppercases `BotRow.mode` from `bot_health` before serialising (`raw_mode.upper() if raw_mode else None`). Root cause is that `bot/mode.py` `BotMode` values are lowercase strings ('panic', 'normal', etc.) stored verbatim in the DB.
- Frontend `getAlertSeverity`, `ModeBadge`, `modeLabel`, and `ModeButtons.normalisedMode` all normalise to uppercase as defensive layers.

**BLOCKER — BotDetailPage mode always null fixed in backend:**
- `bot_detail` endpoint previously hardcoded `mode=None`. Now calls `get_bot_health()` and filters for the symbol's row, populating mode, strategy, status, position_side, error_count — same as `list_bots`.

**MAJOR — error_count exposed and used:**
- Added `error_count: int = 0` to `BotRow` in `api/models.py` and `api-types.d.ts`.
- `list_bots` and `bot_detail` populate it from `h.get('error_count')`.
- Banner now counts `b.error_count > 0` (never `b.status === 'error'` which the engine never writes).

**MAJOR — confirm dialog accessibility:**
- Both `PanicConfirmDialog` (BotDetailPage) and `BulkPanicConfirmDialog` (PortfolioPage) now: Escape → cancel; autoFocus Cancel on mount, restore previous focus on unmount; `aria-labelledby` + `aria-describedby`.

**Why:** Review found banners were permanently showing false alarms (normal bots flagged non-NORMAL) and PANIC was never going critical. Both were silent production bugs introduced by Batch 1 itself.

**How to apply:** Whenever adding mode comparisons in frontend, always normalise `.toUpperCase()` — the DB stores lowercase. Whenever adding a new `bot_health` field to `BotRow`, update both `list_bots` and `bot_detail` endpoints.

Tests: 7 new tests using lowercase mode + error_count fixtures. 203/203 pass. Build clean.
