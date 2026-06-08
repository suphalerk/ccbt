"""Round-1 residual fixes — TDD (fail-first, then implement).

Fix 1: _restore_positions symbol filter
    A DB open row for a DIFFERENT symbol must NOT be restored into this bot.

Fix 2: PnL-magnitude clamp (leverage-relative cap)
    A same-band cross-symbol fill that passes the 5x price-ratio guard but
    yields an implausible pnl_pct (> leverage * 100 %) must still be rejected
    to the reconcile/fallback path — no garbage alert or journal entry.
    A legitimate high-leverage move within the cap must NOT be false-rejected.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Guard: only run when ccxt / anthropic are available (same pattern as
# test_engine_hygiene_round1.py).
try:
    import ccxt        # noqa: F401
    import anthropic   # noqa: F401
    _engine_available = True
except ImportError:
    _engine_available = False

engine_required = pytest.mark.skipif(
    not _engine_available,
    reason="anthropic or ccxt not installed in this venv",
)


# ---------------------------------------------------------------------------
# Helpers shared by both test classes
# ---------------------------------------------------------------------------

def _make_journal_mock() -> MagicMock:
    j = MagicMock()
    j.log_trade_close = MagicMock()
    return j


def _make_risk_mock() -> MagicMock:
    r = MagicMock()
    r.record_trade_result = MagicMock()
    return r


def _make_trade_info(
    entry: float = 1.0,
    sl: float = 0.94,
    tp: float = 1.06,
    size: float = 1000.0,
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
        "atr": 0.05,
        "calibration_id": None,
    }


# ---------------------------------------------------------------------------
# Fix 1: _restore_positions symbol filter
# ---------------------------------------------------------------------------

@engine_required
class TestRestoreSymbolFilter:
    """_restore_positions must only restore DB rows whose symbol matches the
    bot's configured symbol (normalised comparison)."""

    def _make_engine(self, bot_symbol: str) -> "TradingEngine":
        """Build a minimally-wired TradingEngine with no real exchange."""
        from bot.engine import TradingEngine

        config = {
            "symbol": bot_symbol,
            "timeframe_signal": "1h",
            "leverage": 5,
            "risk_per_trade": 0.01,
            "max_daily_loss": 0.05,
            "max_positions": 2,
            "atr_sl_mult": 1.5,
            "atr_tp_mult": 3.0,
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_period": 14,
            "volume_ma_period": 20,
            "min_atr": 0.001,
            "rsi_long_min": 48,
            "rsi_long_max": 75,
            "rsi_short_min": 25,
            "rsi_short_max": 52,
            "use_testnet": True,
            "signals": {},
            "commission_rate": 0.00055,
            "slippage_rate": 0.0002,
        }
        engine = TradingEngine.__new__(TradingEngine)
        engine._config = config
        engine._tracked_trades = {}
        engine._portfolio_manager = None
        engine._recently_closed = None
        # Inject mock client and journal (not yet assigned in __init__ path)
        engine._client = MagicMock()
        engine._journal = MagicMock()
        return engine

    @pytest.mark.asyncio
    async def test_different_symbol_db_trade_not_restored(self):
        """A DB open row for POLUSDT must NOT be restored into a bot
        configured for BTCUSDT — even if the exchange has a matching-side
        position open."""
        from bot.engine import TradingEngine

        bot_symbol = "BTC/USDT:USDT"
        other_symbol = "POL/USDT:USDT"

        engine = self._make_engine(bot_symbol)

        # Exchange has a LONG position open (for the bot's coin)
        engine._client.get_positions.return_value = [
            {"side": "long", "symbol": bot_symbol, "size": 0.01}
        ]

        # DB has one open trade — but it belongs to a DIFFERENT symbol
        engine._journal.get_open_trades.return_value = [
            {
                "id": 99,
                "symbol": other_symbol,   # wrong symbol
                "side": "buy",
                "entry_price": 0.09033,
                "size": 1000.0,
                "stop_loss": 0.085,
                "take_profit": 0.095,
            }
        ]

        await engine._restore_positions()

        # The DB trade for the other symbol must NOT be tracked
        assert 99 not in engine._tracked_trades, (
            f"DB trade for {other_symbol} was incorrectly restored into "
            f"a bot configured for {bot_symbol}"
        )

    @pytest.mark.asyncio
    async def test_correct_symbol_db_trade_is_restored(self):
        """A DB open row for the bot's own symbol IS restored normally."""
        bot_symbol = "BTC/USDT:USDT"

        engine = self._make_engine(bot_symbol)

        engine._client.get_positions.return_value = [
            {"side": "long", "symbol": bot_symbol, "size": 0.01}
        ]

        # DB has a trade for the correct symbol
        engine._journal.get_open_trades.return_value = [
            {
                "id": 42,
                "symbol": bot_symbol,
                "side": "buy",
                "entry_price": 65000.0,
                "size": 0.01,
                "stop_loss": 62000.0,
                "take_profit": 70000.0,
            }
        ]

        # Mock OHLCV fetch to avoid real network call
        import pandas as pd
        import numpy as np

        fake_df = pd.DataFrame(
            {
                "open": [64000.0] * 10,
                "high": [65500.0] * 10,
                "low": [63000.0] * 10,
                "close": [65000.0] * 10,
                "volume": [100.0] * 10,
                "atr": [500.0] * 10,
            }
        )
        engine._client.get_ohlcv.return_value = fake_df

        with patch("bot.engine.add_indicators", return_value=fake_df):
            await engine._restore_positions()

        assert 42 in engine._tracked_trades, (
            "DB trade for the bot's own symbol was NOT restored"
        )

    @pytest.mark.asyncio
    async def test_mixed_db_only_own_symbol_restored(self):
        """When DB has two open trades — one for this bot's symbol, one for
        another — only the matching one is restored."""
        bot_symbol = "BTC/USDT:USDT"
        other_symbol = "ETH/USDT:USDT"

        engine = self._make_engine(bot_symbol)

        engine._client.get_positions.return_value = [
            {"side": "long", "symbol": bot_symbol, "size": 0.01}
        ]

        engine._journal.get_open_trades.return_value = [
            {
                "id": 10,
                "symbol": bot_symbol,   # should be restored
                "side": "buy",
                "entry_price": 65000.0,
                "size": 0.01,
                "stop_loss": 62000.0,
                "take_profit": 70000.0,
            },
            {
                "id": 20,
                "symbol": other_symbol,  # must NOT be restored
                "side": "buy",
                "entry_price": 3000.0,
                "size": 0.5,
                "stop_loss": 2800.0,
                "take_profit": 3300.0,
            },
        ]

        import pandas as pd

        fake_df = pd.DataFrame(
            {
                "open": [64000.0] * 10,
                "high": [65500.0] * 10,
                "low": [63000.0] * 10,
                "close": [65000.0] * 10,
                "volume": [100.0] * 10,
                "atr": [500.0] * 10,
            }
        )
        engine._client.get_ohlcv.return_value = fake_df

        with patch("bot.engine.add_indicators", return_value=fake_df):
            await engine._restore_positions()

        assert 10 in engine._tracked_trades, "Own-symbol trade must be restored"
        assert 20 not in engine._tracked_trades, (
            f"Trade for {other_symbol} must NOT be restored into {bot_symbol} bot"
        )


# ---------------------------------------------------------------------------
# Fix 2: PnL-magnitude clamp (leverage-relative guard)
# ---------------------------------------------------------------------------

class TestPnlMagnitudeClamp:
    """A fill whose price passes the _MAX_EXIT_RATIO=5.0 guard but still
    yields an implausible pnl_pct (> leverage * 100 %) must be rejected.

    Context: POL entry=0.09, AXS price=0.30 → ratio=3.3x < 5.0 (passes
    price-ratio guard).  But PnL would be ~+233% for a 5x-leveraged bot,
    which is implausible for a single candle on a non-gap market.
    """

    def _run_check(
        self,
        entry: float,
        exit_price: float,
        leverage: int = 5,
        size: float = 1000.0,
        side: str = "buy",
        extra_config: Optional[dict] = None,
    ):
        """Run check_closed_positions with given prices and return
        (result, journal_kwargs, alert_calls)."""
        from bot.engine import check_closed_positions

        config = {"leverage": leverage}
        if extra_config:
            config.update(extra_config)

        sl = entry * 0.94
        tp = entry * 1.06
        info = _make_trade_info(entry=entry, sl=sl, tp=tp, size=size, side=side)

        journal = _make_journal_mock()
        risk_mgr = _make_risk_mock()

        close_side = "sell" if side == "buy" else "buy"
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": close_side, "price": exit_price, "amount": size}
        ]
        # Ticker returns a price near entry (sane), not the garbage exit
        client.get_ticker_price.return_value = entry * 0.96
        client.cancel_all_orders.return_value = None

        alert_calls: list = []
        with patch(
            "bot.engine.send_alert",
            side_effect=lambda msg, **kw: alert_calls.append(msg),
        ):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol="POL/USDT:USDT",
                client=client,
                last_trade_close={},
                config=config,
            )

        journal_kwargs = journal.log_trade_close.call_args[1] if journal.log_trade_close.called else {}
        return result, journal_kwargs, alert_calls

    # The single-candle price-move ceiling (must match engine._MAX_CANDLE_MOVE_PCT).
    _MOVE_CAP_PCT = 50.0

    def test_same_band_contamination_3x_rejected(self):
        """POL entry=0.09, contaminated exit=0.30 → ratio=3.3x (passes the 5x
        price-ratio guard) → +233% single-candle move.  This is rejected by the
        leverage-INDEPENDENT single-candle move ceiling (50%), so it must hold at
        the REAL deployed leverage (25x), not only at the toy leverage=2 the old
        (mis-unit'd) leverage*100 cap needed.
        """
        entry = 0.09
        contaminated_exit = 0.30  # 3.33x entry (passes _MAX_EXIT_RATIO=5.0); +233% move
        leverage = 25  # the deployed fleet leverage — the old cap (2500%) never fired here

        result, journal_kwargs, alert_calls = self._run_check(
            entry=entry, exit_price=contaminated_exit, leverage=leverage
        )

        # Close must still be detected
        assert 1 not in result, "Close detection must still fire"

        # The +233% garbage must NOT be journaled — it falls to the bounded
        # ticker/SL/TP reconcile path, so |pnl_pct| is far below the 50% ceiling.
        pnl_pct = abs(journal_kwargs.get("pnl_pct", 0))
        assert pnl_pct <= self._MOVE_CAP_PCT, (
            f"Journaled pnl_pct={pnl_pct:.1f}% exceeds the single-candle move "
            f"ceiling {self._MOVE_CAP_PCT}% at deployed leverage {leverage}"
        )

    def test_same_band_contamination_no_garbage_alert(self):
        """The implausible +233% must not appear in any Telegram alert — at the
        REAL deployed leverage (25x)."""
        import re

        entry = 0.09
        contaminated_exit = 0.30
        leverage = 25  # deployed leverage

        _, _, alert_calls = self._run_check(
            entry=entry, exit_price=contaminated_exit, leverage=leverage
        )

        for msg in alert_calls:
            pct_matches = re.findall(r"([+-]?[\d,]+\.?\d*)\s*%", msg)
            for m in pct_matches:
                val = float(m.replace(",", ""))
                assert abs(val) <= self._MOVE_CAP_PCT, (
                    f"Alert contains implausible PnL {val:.1f}% "
                    f"(> {self._MOVE_CAP_PCT}% single-candle ceiling): {msg!r}"
                )

    def test_legitimate_high_leverage_move_not_false_rejected(self):
        """A real high-leverage TP hit must NOT be suppressed.

        Scenario: 10x leverage, entry=100, TP=106 (+6%).
        With 10x leverage the PnL on position-size basis is ~+6%.
        pnl_pct (as pnl/size*100) = 6%  << cap=1000%.  Must NOT be rejected.
        """
        entry = 100.0
        tp_exit = 106.0  # legitimate TP hit, 6% move
        leverage = 10    # cap = 1000 %

        result, journal_kwargs, alert_calls = self._run_check(
            entry=entry,
            exit_price=tp_exit,
            leverage=leverage,
            size=1000.0,
        )

        # Trade must be closed and journaled normally
        assert 1 not in result, "Legitimate close must still be detected"
        assert journal_kwargs.get("exit_price") == pytest.approx(tp_exit, rel=1e-4), (
            f"Legitimate TP exit_price {tp_exit} was not journaled; "
            f"got {journal_kwargs.get('exit_price')}"
        )
        # pnl_pct must be the real number, not zeroed/None
        pnl_pct = journal_kwargs.get("pnl_pct", None)
        assert pnl_pct is not None and abs(pnl_pct) > 0, (
            "Legitimate close must have a non-zero pnl_pct"
        )
        # Must not exceed cap (confirms no over-rejection logic bug)
        assert abs(pnl_pct) <= leverage * 100, (
            f"pnl_pct={pnl_pct} for legitimate close exceeds cap={leverage * 100}"
        )
        assert len(alert_calls) == 1, "Legitimate close must generate exactly one alert"

    def test_no_config_arg_uses_default_cap(self):
        """When config is not passed (None/absent), the guard uses a safe
        default cap (e.g. 1000%) so normal closes are never suppressed."""
        from bot.engine import check_closed_positions

        entry = 30000.0
        tp_exit = 32000.0  # 6.67% move
        info = _make_trade_info(entry=entry, sl=28000.0, tp=tp_exit, size=100.0)

        journal = _make_journal_mock()
        risk_mgr = _make_risk_mock()
        client = MagicMock()
        client.get_closed_pnl.return_value = [
            {"side": "sell", "price": tp_exit, "amount": 100.0}
        ]
        client.get_ticker_price.return_value = tp_exit
        client.cancel_all_orders.return_value = None

        with patch("bot.engine.send_alert"):
            result = check_closed_positions(
                open_trade_ids={1: info},
                current_positions=[],
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=None,
                symbol="BTC/USDT:USDT",
                client=client,
                # config NOT passed — must default safely
            )

        assert 1 not in result, "Close must be detected even without config arg"
        journal.log_trade_close.assert_called_once()
        kwargs = journal.log_trade_close.call_args[1]
        assert abs(kwargs.get("exit_price", 0) - tp_exit) < 1.0
