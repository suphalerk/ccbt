"""Engine latent-minors Round 1 — TDD regression tests.

Three latent issues fixed in bot/engine.py (external-close review):

  FIX1 (DEDUP KEY): recently_closed registry was keyed by normalised symbol
        only.  Changed to (norm_symbol, trade_side) so opposite-side bots
        (hedge / long-bias + short-bias on the same coin) each get their own
        journal row and alert, while same-side bots sharing one netted position
        still dedup correctly.

  FIX2 (API-HALT SILENT CLOSE FAILURE): cancel_all_orders / close_all_positions
        at the API-error halt path were wrapped in a bare ``except Exception: pass``.
        A failed emergency close left a live unprotected position with NO log or
        alert.  Changed to log CRITICAL + send_alert (non-raising).

  FIX3 (PER-STRATEGY PnL COMMENT): code comment added at the dedup/journal site
        documenting that per-strategy attribution is approximate for shared-symbol
        coins (AXS=7, FIL=3).  No code change — tested by reading the source file.

All tests are pure-unit — no exchange, no network, no SQLite on disk.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

try:
    import anthropic  # noqa: F401
    import ccxt  # noqa: F401
    _engine_available = True
except ImportError:
    _engine_available = False

engine_required = pytest.mark.skipif(
    not _engine_available,
    reason="anthropic or ccxt not installed in this venv",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_trade_info(
    entry: float = 5.0,
    sl: float = 4.5,
    tp: float = 5.8,
    size: float = 500.0,
    side: str = "buy",
    open_time: Optional[float] = None,
) -> dict:
    return {
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "size": size,
        "open_time": open_time or (time.time() - 3600),
        "signal_type": "ema_crossover",
        "atr": 0.3,
        "calibration_id": None,
    }


def _make_journal() -> MagicMock:
    j = MagicMock()
    j.log_trade_close = MagicMock()
    return j


def _make_risk_mgr() -> MagicMock:
    r = MagicMock()
    r.record_trade_result = MagicMock()
    return r


def _run_check(
    trade_id: int,
    symbol: str,
    trade_info: dict,
    exit_price: float,
    recently_closed: Optional[dict],
    journal: Optional[MagicMock] = None,
    risk_mgr: Optional[MagicMock] = None,
    alert_calls: Optional[list] = None,
) -> dict:
    """Invoke check_closed_positions for a single bot detecting one closed trade."""
    from bot.engine import check_closed_positions

    if journal is None:
        journal = _make_journal()
    if risk_mgr is None:
        risk_mgr = _make_risk_mgr()

    client = MagicMock()
    client.get_closed_pnl.return_value = [
        {"side": "sell" if trade_info["side"] == "buy" else "buy",
         "price": exit_price, "amount": trade_info["size"]}
    ]
    client.get_ticker_price.return_value = exit_price
    client.cancel_all_orders.return_value = None

    local_calls: list = [] if alert_calls is None else alert_calls
    with patch("bot.engine.send_alert", side_effect=lambda msg, **kw: local_calls.append(msg)):
        result = check_closed_positions(
            open_trade_ids={trade_id: trade_info},
            current_positions=[],   # position gone from exchange
            journal=journal,
            risk_mgr=risk_mgr,
            calibration_tracker=None,
            symbol=symbol,
            client=client,
            last_trade_close={},
            recently_closed=recently_closed,
        )
    return result


# ---------------------------------------------------------------------------
# FIX1 — Dedup key is (norm_symbol, trade_side), not norm_symbol alone
# ---------------------------------------------------------------------------

@engine_required
class TestDedupKeyIncludesSide:
    """opposite-side bots on the same coin must NOT be deduped; same-side bots
    sharing one netted position must still dedup to exactly one journal + alert."""

    SYMBOL = "AXS/USDT:USDT"
    ENTRY = 5.0
    SL = 4.5
    TP = 5.8

    def test_opposite_sides_both_journal_and_alert(self):
        """Long bot + short bot on AXS both close within the TTL window.
        Both must produce a journal row and an alert (not deduped)."""
        recently_closed: dict = {}
        alert_calls: list = []

        long_journal = _make_journal()
        short_journal = _make_journal()

        # Long bot closes (SL hit for long)
        long_info = _make_trade_info(side="buy", entry=self.ENTRY,
                                     sl=self.SL, tp=self.TP)
        _run_check(
            trade_id=1001,
            symbol=self.SYMBOL,
            trade_info=long_info,
            exit_price=self.SL,
            recently_closed=recently_closed,
            journal=long_journal,
            alert_calls=alert_calls,
        )

        # Short bot closes (TP hit for short, within TTL)
        short_info = _make_trade_info(side="sell", entry=self.ENTRY,
                                      sl=self.TP, tp=self.SL)
        _run_check(
            trade_id=1002,
            symbol=self.SYMBOL,
            trade_info=short_info,
            exit_price=self.SL,
            recently_closed=recently_closed,
            journal=short_journal,
            alert_calls=alert_calls,
        )

        # Both sides must have journaled independently
        assert long_journal.log_trade_close.call_count == 1, (
            f"Long bot journal missing (count={long_journal.log_trade_close.call_count})"
        )
        assert short_journal.log_trade_close.call_count == 1, (
            f"Short bot journal missing (count={short_journal.log_trade_close.call_count})"
        )

        # Two separate alerts (one per side)
        assert len(alert_calls) == 2, (
            f"Expected 2 alerts (one per side), got {len(alert_calls)}: {alert_calls}"
        )

    def test_same_side_bots_still_dedup_to_one(self):
        """Two long bots on AXS (simulating 7-config shared netted position)
        must produce exactly one journal row and one alert."""
        recently_closed: dict = {}
        alert_calls: list = []

        journal1 = _make_journal()
        journal2 = _make_journal()

        long_info = _make_trade_info(side="buy", entry=self.ENTRY,
                                     sl=self.SL, tp=self.TP)

        _run_check(
            trade_id=2001,
            symbol=self.SYMBOL,
            trade_info=long_info,
            exit_price=self.TP,
            recently_closed=recently_closed,
            journal=journal1,
            alert_calls=alert_calls,
        )
        _run_check(
            trade_id=2002,
            symbol=self.SYMBOL,
            trade_info=long_info,
            exit_price=self.TP,
            recently_closed=recently_closed,
            journal=journal2,
            alert_calls=alert_calls,
        )

        total_journal = (
            journal1.log_trade_close.call_count
            + journal2.log_trade_close.call_count
        )
        assert total_journal == 1, (
            f"Same-side dedup failed — expected 1 journal row, got {total_journal}"
        )
        assert len(alert_calls) == 1, (
            f"Same-side dedup failed — expected 1 alert, got {len(alert_calls)}"
        )

    def test_dedup_key_is_tuple_in_registry(self):
        """After a successful close, recently_closed must contain a tuple key
        (norm_symbol, trade_side) rather than a plain string."""
        recently_closed: dict = {}

        long_info = _make_trade_info(side="buy", entry=self.ENTRY,
                                     sl=self.SL, tp=self.TP)
        with patch("bot.engine.send_alert"):
            _run_check(
                trade_id=3001,
                symbol=self.SYMBOL,
                trade_info=long_info,
                exit_price=self.SL,
                recently_closed=recently_closed,
            )

        # Registry must have exactly one tuple key, not a plain string
        assert len(recently_closed) == 1, (
            f"Expected 1 registry entry, got {len(recently_closed)}"
        )
        key = next(iter(recently_closed))
        assert isinstance(key, tuple), (
            f"Dedup key must be a tuple (norm_symbol, side), got {type(key)}: {key!r}"
        )
        assert len(key) == 2, f"Key must be 2-tuple, got length {len(key)}: {key!r}"
        # First element should be the normalised symbol (no slashes/colons)
        norm_sym, side = key
        assert "/" not in norm_sym and ":" not in norm_sym, (
            f"Symbol in key not normalised: {norm_sym!r}"
        )
        assert side in ("long", "short"), (
            f"Side in key must be 'long' or 'short', got {side!r}"
        )

    def test_opposite_sides_different_keys(self):
        """Verify that a long close and a short close on the same coin
        produce two distinct tuple keys in the registry."""
        recently_closed: dict = {}

        long_info = _make_trade_info(side="buy", entry=self.ENTRY,
                                     sl=self.SL, tp=self.TP)
        short_info = _make_trade_info(side="sell", entry=self.ENTRY,
                                      sl=self.TP, tp=self.SL)

        with patch("bot.engine.send_alert"):
            _run_check(
                trade_id=4001,
                symbol=self.SYMBOL,
                trade_info=long_info,
                exit_price=self.SL,
                recently_closed=recently_closed,
            )
            _run_check(
                trade_id=4002,
                symbol=self.SYMBOL,
                trade_info=short_info,
                exit_price=self.SL,
                recently_closed=recently_closed,
            )

        assert len(recently_closed) == 2, (
            f"Expected 2 registry keys (long + short), got {len(recently_closed)}: "
            f"{list(recently_closed.keys())}"
        )
        sides_in_keys = {k[1] for k in recently_closed}
        assert sides_in_keys == {"long", "short"}, (
            f"Expected keys for both 'long' and 'short', got {sides_in_keys}"
        )


# ---------------------------------------------------------------------------
# FIX2 — API-halt emergency close failure must log CRITICAL and alert
# ---------------------------------------------------------------------------

@engine_required
class TestApiHaltCloseFailureLogs:
    """When cancel_all_orders or close_all_positions raises during an API-error
    halt, the bot must emit a CRITICAL log and a send_alert call — not silently
    swallow the exception.  The method must still return True (halt the loop)."""

    def _make_engine(self, symbol: str = "BTC/USDT:USDT") -> object:
        """Build a minimal TradingEngine with mocked dependencies."""
        from bot.engine import TradingEngine

        config = {
            "symbol": symbol,
            "timeframe_signal": "1h",
            "timeframe_trend": "4h",
            "ema_fast": 9,
            "ema_slow": 21,
            "ema_trend": 50,
            "rsi_period": 14,
            "atr_period": 14,
            "atr_sl_mult": 1.5,
            "atr_tp_mult": 3.0,
            "risk_per_trade": 0.01,
            "max_daily_loss": 0.03,
            "max_positions": 2,
            "leverage": 3,
            "use_testnet": True,
            "exchange": "binance",
            "signals": {},
        }

        client = MagicMock()
        client.cancel_all_orders = MagicMock(
            side_effect=RuntimeError("exchange offline")
        )
        client.close_all_positions = MagicMock()

        journal = MagicMock()
        risk_mgr = MagicMock()
        risk_mgr.record_api_error = MagicMock()
        # Simulate halt condition: can_trade returns False with "API error" in reason
        risk_mgr.can_trade = MagicMock(return_value=(False, "API error threshold exceeded"))
        risk_mgr.state = MagicMock()
        risk_mgr.state.consecutive_api_errors = 6

        engine = TradingEngine.__new__(TradingEngine)
        engine._config = config
        engine._client = client
        engine._risk_mgr = risk_mgr
        engine._journal = journal
        engine._tracked_trades = {}
        engine._balance = 1000.0
        engine._portfolio_manager = None
        engine._shutdown_event = MagicMock()
        engine._interruptible_sleep = AsyncMock(return_value=False)

        return engine

    @pytest.mark.asyncio
    async def test_api_halt_close_fails_emits_critical_log(self):
        """When close_all_positions raises during API-error halt, a CRITICAL
        log with key 'api_halt_close_failed' must be emitted."""
        engine = self._make_engine()

        alert_calls: list = []
        with patch("bot.engine.send_alert",
                   side_effect=lambda msg, **kw: alert_calls.append(msg)) as mock_alert, \
             patch("bot.engine.logger") as mock_logger:
            result = await engine._handle_error(RuntimeError("network timeout"))

        # The CRITICAL log for close failure must have been called
        critical_calls = [
            c for c in mock_logger.critical.call_args_list
            if c.args and c.args[0] == "api_halt_close_failed"
        ]
        assert critical_calls, (
            f"Expected logger.critical('api_halt_close_failed', ...) but got: "
            f"{mock_logger.critical.call_args_list}"
        )

    @pytest.mark.asyncio
    async def test_api_halt_close_fails_sends_alert(self):
        """When close_all_positions raises during API-error halt, send_alert
        must be called with a message indicating the emergency close failed."""
        engine = self._make_engine()

        alert_calls: list = []
        with patch("bot.engine.send_alert",
                   side_effect=lambda msg, **kw: alert_calls.append(msg)):
            with patch("bot.engine.logger"):
                result = await engine._handle_error(RuntimeError("timeout"))

        # At least one alert must mention the close failure
        close_fail_alerts = [a for a in alert_calls if "close" in a.lower()
                             and ("fail" in a.lower() or "critical" in a.lower())]
        assert close_fail_alerts, (
            f"Expected an alert about emergency close failure, got: {alert_calls}"
        )

    @pytest.mark.asyncio
    async def test_api_halt_close_fails_still_returns_true(self):
        """Even when the emergency close raises, _handle_error must return True
        (halt the loop) — not crash, not return False."""
        engine = self._make_engine()

        with patch("bot.engine.send_alert"):
            with patch("bot.engine.logger"):
                result = await engine._handle_error(RuntimeError("connection reset"))

        assert result is True, (
            f"_handle_error must return True (halt) even when close fails, got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_api_halt_close_succeeds_no_extra_critical(self):
        """When cancel_all_orders and close_all_positions succeed (normal halt),
        no 'api_halt_close_failed' critical log must be emitted."""
        engine = self._make_engine()
        # Override: close succeeds this time
        engine._client.cancel_all_orders = MagicMock()
        engine._client.close_all_positions = MagicMock()

        with patch("bot.engine.send_alert"):
            with patch("bot.engine.logger") as mock_logger:
                result = await engine._handle_error(RuntimeError("timeout"))

        close_fail_calls = [
            c for c in mock_logger.critical.call_args_list
            if c.args and c.args[0] == "api_halt_close_failed"
        ]
        assert not close_fail_calls, (
            f"No 'api_halt_close_failed' should be logged on a successful close, "
            f"got: {close_fail_calls}"
        )
        assert result is True


# ---------------------------------------------------------------------------
# FIX3 — PnL attribution comment exists in source
# ---------------------------------------------------------------------------

class TestPnlAttributionComment:
    """Verify the code comment documenting the per-strategy PnL attribution
    limitation is present at the dedup/journal site in bot/engine.py."""

    ENGINE_PATH = REPO / "bot" / "engine.py"

    def test_attribution_comment_present(self):
        """The source file must contain a note about per-strategy PnL being
        approximate for shared-symbol coins."""
        source = self.ENGINE_PATH.read_text(encoding="utf-8")
        # Check for key phrases from the required comment
        assert "PER-STRATEGY PnL ATTRIBUTION" in source, (
            "Comment 'PER-STRATEGY PnL ATTRIBUTION' not found in bot/engine.py. "
            "Add a comment at the dedup/journal site explaining that per-strategy "
            "attribution is approximate for shared-symbol coins."
        )

    def test_attribution_comment_mentions_netting(self):
        """The comment must mention netting (the root cause of the approximation)."""
        source = self.ENGINE_PATH.read_text(encoding="utf-8")
        assert "netting" in source.lower() or "netted" in source.lower(), (
            "Attribution comment should mention exchange netting as the root cause."
        )

    def test_attribution_comment_near_dedup_site(self):
        """The comment must be in the same function as the dedup logic
        (check_closed_positions) rather than in an unrelated place."""
        source = self.ENGINE_PATH.read_text(encoding="utf-8")
        # Find the function and the comment within it
        func_start = source.find("def check_closed_positions(")
        assert func_start != -1, "check_closed_positions not found"
        # Next function definition after it
        next_def = source.find("\ndef ", func_start + 1)
        func_body = source[func_start:next_def if next_def != -1 else len(source)]
        assert "PER-STRATEGY PnL ATTRIBUTION" in func_body, (
            "Attribution comment must be inside check_closed_positions, not elsewhere."
        )
