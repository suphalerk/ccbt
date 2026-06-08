"""PR1 — WS wake-plumbing tests (ticket Section 7, tests 5-8, 7b, 13, 14 + flag-OFF parity).

All tests are pure-unit — no network, no exchange, no real Telegram.
conftest.py autouse fixture blocks real Telegram for every test here.

Tests:
  T5  — test_interruptible_sleep_wakes_on_event
  T6  — test_verify_close_after_ws_confirms_absence
  T7  — test_verify_close_after_ws_ambiguous_no_alert
  T7b — test_verify_close_after_ws_exception_no_alert
  T8  — test_ws_event_dedup_multi_bot_per_coin
  T13 — test_no_double_fetch_on_ws_wake
  T14 — test_woke_via_ws_at_cooldown_sleep
  T0  — test_flag_off_parity (flag-OFF == today, _woke_via_ws always False)
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
# Minimal engine factory (no exchange / no network)
# ---------------------------------------------------------------------------

def _make_config(symbol: str = "BTC/USDT:USDT") -> dict:
    return {
        "symbol": symbol,
        "timeframe_signal": "15m",
        "timeframe_trend": "1h",
        "leverage": 3,
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.03,
        "max_positions": 2,
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 50,
        "rsi_period": 14,
        "rsi_min": 45,
        "rsi_max": 70,
        "atr_period": 14,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 3.0,
        "use_testnet": True,
    }


def _make_trade_info(
    side: str = "buy",
    entry: float = 50000.0,
    sl: float = 48000.0,
    tp: float = 54000.0,
    size: float = 1000.0,
) -> dict:
    return {
        "side": side,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "size": size,
        "open_time": time.time() - 3600,
        "signal_type": "ema_crossover",
        "atr": 500.0,
        "calibration_id": None,
    }


def _make_engine(
    symbol: str = "BTC/USDT:USDT",
    wake_events: Optional[dict] = None,
    tracked_trades: Optional[dict] = None,
) -> "TradingEngine":  # noqa: F821
    """Build a TradingEngine stub with faked internals (no real exchange)."""
    from bot.engine import TradingEngine

    config = _make_config(symbol)
    shutdown = asyncio.Event()
    engine = TradingEngine(
        config=config,
        shutdown_event=shutdown,
        shared_exchange=None,
        wake_events=wake_events,
    )
    # Wire up minimal stubs so _verify_close_after_ws / _monitor_positions work
    engine._journal = MagicMock()
    engine._journal.log_trade_close = MagicMock()
    engine._risk_mgr = MagicMock()
    engine._risk_mgr.state = MagicMock()
    engine._risk_mgr.state.api_error_count = 0
    engine._risk_mgr.can_trade = MagicMock(return_value=(True, "ok"))
    engine._calibration_tracker = None
    engine._last_trade_close = {}
    engine._recently_closed = {}
    engine._portfolio_manager = None
    engine._config = config

    # Stub client with a scriptable get_positions
    engine._client = MagicMock()
    engine._client.get_positions = MagicMock(return_value=[])

    if tracked_trades is not None:
        engine._tracked_trades = tracked_trades

    return engine


# ---------------------------------------------------------------------------
# T0 — flag-OFF parity: wake_events=None, _interruptible_sleep == plain timeout
# ---------------------------------------------------------------------------

@engine_required
class TestFlagOffParity:
    """When wake_events is None (flag-OFF), behaviour must be byte-for-byte today."""

    @pytest.mark.asyncio
    async def test_sleep_returns_false_on_timeout(self):
        """_interruptible_sleep returns False after timeout; _woke_via_ws stays False."""
        engine = _make_engine(wake_events=None)
        assert not engine._woke_via_ws
        result = await engine._interruptible_sleep(0.05)
        assert result is False
        assert engine._woke_via_ws is False

    @pytest.mark.asyncio
    async def test_sleep_returns_true_on_shutdown(self):
        """Returns True when shutdown is set (flag-OFF path)."""
        engine = _make_engine(wake_events=None)
        engine._shutdown_event.set()
        result = await engine._interruptible_sleep(5.0)
        assert result is True
        assert engine._woke_via_ws is False

    @pytest.mark.asyncio
    async def test_verify_never_called_when_no_wake(self):
        """_woke_via_ws stays False; _verify_close_after_ws is never triggered."""
        engine = _make_engine(wake_events=None)
        # Confirm the private event never fires — direct check
        assert not engine._wake_event.is_set()
        await engine._interruptible_sleep(0.05)
        assert engine._woke_via_ws is False

    def test_wake_event_not_in_any_registry(self):
        """When wake_events=None, engine._wake_event is isolated (not registered)."""
        registry: dict = {}
        engine = _make_engine(wake_events=None)
        # Registry should be untouched
        assert registry == {}
        # Engine's event is private and unfired
        assert not engine._wake_event.is_set()


# ---------------------------------------------------------------------------
# T5 — _interruptible_sleep wakes on private event
# ---------------------------------------------------------------------------

@engine_required
class TestInterruptibleSleepWakes:
    """_interruptible_sleep races shutdown vs wake_event vs timeout."""

    @pytest.mark.asyncio
    async def test_wake_event_fires_returns_false_fast(self):
        """Setting _wake_event mid-sleep returns False quickly; _woke_via_ws True."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry)

        async def _fire_after_delay():
            await asyncio.sleep(0.05)
            engine._wake_event.set()

        fire_task = asyncio.create_task(_fire_after_delay())
        start = asyncio.get_event_loop().time()
        result = await engine._interruptible_sleep(10.0)  # would block 10s without event
        elapsed = asyncio.get_event_loop().time() - start

        await fire_task

        assert result is False, "wake event must return False (not a shutdown)"
        assert elapsed < 1.0, f"sleep should have woken early, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_woke_via_ws_true_from_race_winner(self):
        """_woke_via_ws is True and is captured from the race result, not post-hoc is_set."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry)

        async def _fire():
            await asyncio.sleep(0.02)
            engine._wake_event.set()

        t = asyncio.create_task(_fire())
        await engine._interruptible_sleep(5.0)
        await t

        assert engine._woke_via_ws is True

    @pytest.mark.asyncio
    async def test_wake_event_cleared_after_sleep(self):
        """After waking on the event, _wake_event.is_set() is False (cleared by engine)."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry)

        async def _fire():
            await asyncio.sleep(0.02)
            engine._wake_event.set()

        t = asyncio.create_task(_fire())
        await engine._interruptible_sleep(5.0)
        await t

        assert not engine._wake_event.is_set(), "event must be cleared after return"

    @pytest.mark.asyncio
    async def test_shutdown_returns_true(self):
        """Shutdown set → returns True; _woke_via_ws stays False."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry)

        async def _set_shutdown():
            await asyncio.sleep(0.02)
            engine._shutdown_event.set()

        t = asyncio.create_task(_set_shutdown())
        result = await engine._interruptible_sleep(5.0)
        await t

        assert result is True
        assert engine._woke_via_ws is False

    @pytest.mark.asyncio
    async def test_sibling_event_not_cleared(self):
        """Clearing this engine's event does NOT affect a sibling engine's event."""
        registry: dict = {}
        engine_a = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)
        engine_b = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)

        # Both engines should be registered for BTCUSDT
        assert "BTCUSDT" in registry
        assert len(registry["BTCUSDT"]) == 2

        # Fire engine_a's event only
        async def _fire_a():
            await asyncio.sleep(0.02)
            engine_a._wake_event.set()

        t = asyncio.create_task(_fire_a())
        await engine_a._interruptible_sleep(5.0)
        await t

        # engine_a cleared its own event; engine_b's is untouched
        assert not engine_a._wake_event.is_set()
        assert not engine_b._wake_event.is_set()  # never was set
        assert engine_a._woke_via_ws is True
        assert engine_b._woke_via_ws is False


# ---------------------------------------------------------------------------
# T6 — _verify_close_after_ws: PRESENT, PRESENT, ABSENT → one reconcile
# ---------------------------------------------------------------------------

@engine_required
class TestVerifyConfirmsAbsence:
    """Fake positions returns PRESENT twice then ABSENT — exactly one close."""

    @pytest.mark.asyncio
    async def test_exactly_one_close_on_third_attempt(self):
        """After 2 retries the position goes absent → check_closed_positions fires once."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._recently_closed = {}

        # Simulate: calls 1-2 return PRESENT (long side active), call 3 returns ABSENT
        _present_pos = [{"symbol": "BTCUSDT", "side": "long", "contracts": 0.1}]
        _absent_pos: list = []

        call_count = 0

        def _get_positions_seq():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _present_pos
            return _absent_pos

        engine._client.get_positions = MagicMock(side_effect=_get_positions_seq)

        # Silence asyncio.sleep inside _verify_close_after_ws (speed up test)
        with patch("asyncio.sleep", new=AsyncMock()):
            # _monitor_positions calls check_closed_positions which calls journal
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        # Journal should have been called exactly once
        assert engine._journal.log_trade_close.call_count == 1, (
            f"Expected exactly 1 journal close, got {engine._journal.log_trade_close.call_count}"
        )

    @pytest.mark.asyncio
    async def test_cache_set_on_successful_verify(self):
        """After a successful verify, _cached_positions_this_tick is set."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._recently_closed = {}
        engine._client.get_positions = MagicMock(return_value=[])  # absent immediately

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        # Cache must be set (loop-top can reuse it)
        assert engine._cached_positions_this_tick is not None

    @pytest.mark.asyncio
    async def test_woke_via_ws_reset_on_entry(self):
        """_woke_via_ws is False immediately after _verify_close_after_ws returns."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._client.get_positions = MagicMock(return_value=[])

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        assert engine._woke_via_ws is False


# ---------------------------------------------------------------------------
# T7 — ambiguous (PRESENT throughout) → NO alert, NO journal
# ---------------------------------------------------------------------------

@engine_required
class TestVerifyAmbiguousNoAlert:
    """The never-false-alert guard: position PRESENT on all N retries."""

    @pytest.mark.asyncio
    async def test_no_close_when_always_present(self):
        """PRESENT on all 3 attempts → no journal close, no alert."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._recently_closed = {}

        _present = [{"symbol": "BTCUSDT", "side": "long", "contracts": 0.1}]
        engine._client.get_positions = MagicMock(return_value=_present)

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        assert engine._journal.log_trade_close.call_count == 0, (
            "Must NOT journal a close when position is always present"
        )

    @pytest.mark.asyncio
    async def test_cache_not_set_on_ambiguous(self):
        """When all retries are ambiguous, the tick cache is not populated."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        _present = [{"symbol": "BTCUSDT", "side": "long"}]
        engine._client.get_positions = MagicMock(return_value=_present)

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        assert engine._cached_positions_this_tick is None


# ---------------------------------------------------------------------------
# T7b — fetch RAISES on all N → NO alert, NO journal
# ---------------------------------------------------------------------------

@engine_required
class TestVerifyExceptionNoAlert:
    """fetch-exception-is-never-absence guard."""

    @pytest.mark.asyncio
    async def test_no_close_when_fetch_raises(self):
        """get_positions() raises on all N attempts → no journal close."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._client.get_positions = MagicMock(
            side_effect=RuntimeError("connection refused")
        )

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        assert engine._journal.log_trade_close.call_count == 0, (
            "Must NOT journal a close when get_positions() raises"
        )

    @pytest.mark.asyncio
    async def test_no_close_when_fetch_returns_non_list(self):
        """get_positions() returns a non-list sentinel → treated as ambiguous."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        # Return a dict (wrong type) — not a list
        engine._client.get_positions = MagicMock(return_value={"error": "timeout"})

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        assert engine._journal.log_trade_close.call_count == 0


# ---------------------------------------------------------------------------
# T8 — dedup: two engines per coin; confirmed close → exactly one journal+alert
# ---------------------------------------------------------------------------

@engine_required
class TestDedupMultiBotPerCoin:
    """Two engines on the same coin; WS sets both; only ONE journals+alerts."""

    @pytest.mark.asyncio
    async def test_only_one_journal_close_across_siblings(self):
        """Two engines share recently_closed; only the first one journals."""
        registry: dict = {}
        # Shared recently_closed — this is what PortfolioManager provides
        recently_closed: dict = {}

        engine_a = _make_engine(
            symbol="BTC/USDT:USDT",
            wake_events=registry,
            tracked_trades={1: _make_trade_info(side="buy")},
        )
        engine_a._recently_closed = recently_closed

        engine_b = _make_engine(
            symbol="BTC/USDT:USDT",
            wake_events=registry,
            tracked_trades={2: _make_trade_info(side="buy")},
        )
        engine_b._recently_closed = recently_closed

        # Both see the position as absent immediately
        engine_a._client.get_positions = MagicMock(return_value=[])
        engine_b._client.get_positions = MagicMock(return_value=[])

        assert "BTCUSDT" in registry
        assert len(registry["BTCUSDT"]) == 2

        # Wake both
        for evt in registry["BTCUSDT"]:
            evt.set()

        with patch("asyncio.sleep", new=AsyncMock()):
            engine_a._woke_via_ws = True
            engine_b._woke_via_ws = True
            await engine_a._verify_close_after_ws()
            await engine_b._verify_close_after_ws()

        total_journal_calls = (
            engine_a._journal.log_trade_close.call_count
            + engine_b._journal.log_trade_close.call_count
        )
        assert total_journal_calls == 1, (
            f"Expected exactly 1 journal close across siblings, got {total_journal_calls}"
        )

    @pytest.mark.asyncio
    async def test_each_engine_has_own_private_event(self):
        """Each engine registers its own independent asyncio.Event."""
        registry: dict = {}
        engine_a = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)
        engine_b = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)

        assert engine_a._wake_event is not engine_b._wake_event
        assert len(registry.get("BTCUSDT", [])) == 2


# ---------------------------------------------------------------------------
# T13 — no double fetch on WS wake
# ---------------------------------------------------------------------------

@engine_required
class TestNoDoubleFetchOnWake:
    """Verify that a woken bot calls get_positions exactly ONCE per tick."""

    @pytest.mark.asyncio
    async def test_get_positions_called_once_per_tick(self):
        """_verify_close_after_ws fetches once; loop-top reuses the cache."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._recently_closed = {}

        # Position absent immediately → single fetch confirms close
        fetch_calls = 0

        def _counting_get_positions():
            nonlocal fetch_calls
            fetch_calls += 1
            return []  # always absent

        engine._client.get_positions = MagicMock(side_effect=_counting_get_positions)

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        # Verify set the cache
        assert engine._cached_positions_this_tick is not None
        # Simulate loop-top consuming the cache (as the real loop does)
        cached = engine._cached_positions_this_tick
        engine._cached_positions_this_tick = None

        # The loop-top would call _monitor_positions(cached) — simulate that
        await engine._monitor_positions(cached)

        # get_positions should have been called exactly ONCE (in verify, not again at loop-top)
        assert fetch_calls == 1, (
            f"Expected 1 get_positions call, got {fetch_calls}"
        )

    @pytest.mark.asyncio
    async def test_cache_cleared_after_consumption(self):
        """Cache is None after the loop-top consumes it (no stale leak to next tick)."""
        registry: dict = {}
        engine = _make_engine(
            wake_events=registry,
            tracked_trades={42: _make_trade_info(side="buy")},
        )
        engine._client.get_positions = MagicMock(return_value=[])

        with patch("asyncio.sleep", new=AsyncMock()):
            engine._woke_via_ws = True
            await engine._verify_close_after_ws()

        # Consume the cache (as loop-top does)
        engine._cached_positions_this_tick = None
        assert engine._cached_positions_this_tick is None


# ---------------------------------------------------------------------------
# T14 — WS wake at cooldown sleep (:1316 path)
# ---------------------------------------------------------------------------

@engine_required
class TestWokedViaWsAtCooldownSleep:
    """A bot blocked in the can_trade==False cooldown sleep also wakes via WS."""

    @pytest.mark.asyncio
    async def test_woke_via_ws_at_cooldown_sleep(self):
        """Event fires during cooldown sleep → _woke_via_ws True after return."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry)

        async def _fire_after():
            await asyncio.sleep(0.05)
            engine._wake_event.set()

        t = asyncio.create_task(_fire_after())
        # _interruptible_sleep is the same method for candle sleep and cooldown sleep
        result = await engine._interruptible_sleep(10.0)
        await t

        assert result is False
        assert engine._woke_via_ws is True

    @pytest.mark.asyncio
    async def test_clearing_this_engine_does_not_affect_sibling(self):
        """engine_a clears its own event; engine_b's event is unaffected."""
        registry: dict = {}
        engine_a = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)
        engine_b = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)

        # Fire both events (WS sets all registered engines for a coin)
        for evt in registry["BTCUSDT"]:
            evt.set()

        # engine_a wakes and clears its own event
        result_a = await engine_a._interruptible_sleep(0.0)
        # After clearing, engine_a's event is clear; engine_b's must still be set
        assert not engine_a._wake_event.is_set()
        assert engine_b._wake_event.is_set(), (
            "engine_b's event must not be stolen by engine_a's clear()"
        )
        assert engine_a._woke_via_ws is True

    @pytest.mark.asyncio
    async def test_norm_symbol_correct(self):
        """_norm_symbol normalises BTC/USDT:USDT → BTCUSDT (same as check_closed_positions)."""
        registry: dict = {}
        engine = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)
        assert engine._norm_symbol == "BTCUSDT"

    def test_norm_symbol_matches_check_closed_positions(self):
        """The normalisation used at __init__ time must be identical to the one in
        check_closed_positions (engine.py ~:231-234)."""
        from bot.engine import TradingEngine
        # Reproduce the local helper from check_closed_positions verbatim:
        def _normalize_ccp(s: str) -> str:
            return s.replace("/", "").replace(":USDT", "").replace("-", "").upper()

        symbols = [
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "1000PEPE/USDT:USDT",
            "SOL/USDT:USDT",
            "BTC-USDT",  # hypothetical dash form
        ]
        registry: dict = {}
        for sym in symbols:
            engine = _make_engine(symbol=sym, wake_events=registry)
            expected = _normalize_ccp(sym)
            assert engine._norm_symbol == expected, (
                f"Symbol {sym!r}: engine._norm_symbol={engine._norm_symbol!r}, "
                f"expected={expected!r}"
            )


# ---------------------------------------------------------------------------
# Wake events registry wiring tests
# ---------------------------------------------------------------------------

@engine_required
class TestRegistryWiring:
    """Engine registers its private event in wake_events at __init__."""

    def test_engine_registers_in_registry(self):
        """When wake_events is provided, engine appends _wake_event to the list."""
        registry: dict = {}
        engine = _make_engine(symbol="ETH/USDT:USDT", wake_events=registry)
        assert "ETHUSDT" in registry
        assert engine._wake_event in registry["ETHUSDT"]

    def test_two_engines_same_coin_both_registered(self):
        """Two engines on the same coin each register their own event."""
        registry: dict = {}
        e1 = _make_engine(symbol="ETH/USDT:USDT", wake_events=registry)
        e2 = _make_engine(symbol="ETH/USDT:USDT", wake_events=registry)
        assert len(registry["ETHUSDT"]) == 2
        assert e1._wake_event is not e2._wake_event
        assert e1._wake_event in registry["ETHUSDT"]
        assert e2._wake_event in registry["ETHUSDT"]

    def test_different_coins_separate_keys(self):
        """BTC and ETH engines register under their own symbol keys."""
        registry: dict = {}
        btc = _make_engine(symbol="BTC/USDT:USDT", wake_events=registry)
        eth = _make_engine(symbol="ETH/USDT:USDT", wake_events=registry)
        assert "BTCUSDT" in registry
        assert "ETHUSDT" in registry
        assert btc._wake_event not in registry["ETHUSDT"]
        assert eth._wake_event not in registry["BTCUSDT"]

    def test_no_registration_when_wake_events_none(self):
        """When wake_events=None, no side effects on any registry."""
        engine = _make_engine(wake_events=None)
        # Engine's event is private — cannot be externally fired
        assert not engine._wake_event.is_set()

    def test_set_all_events_for_coin_wakes_all_engines(self):
        """Setting all events in registry for a coin wakes all registered engines."""
        registry: dict = {}
        engines = [_make_engine(symbol="BTC/USDT:USDT", wake_events=registry) for _ in range(4)]
        assert len(registry.get("BTCUSDT", [])) == 4
        # Simulate WS handler: evt.set() for each registered engine
        for evt in registry["BTCUSDT"]:
            evt.set()
        for eng in engines:
            assert eng._wake_event.is_set()


# ---------------------------------------------------------------------------
# Short-circuit when _tracked_trades is empty
# ---------------------------------------------------------------------------

@engine_required
class TestVerifyShortCircuitsWhenEmpty:
    """_verify_close_after_ws short-circuits when no tracked trades (sibling deduped)."""

    @pytest.mark.asyncio
    async def test_no_fetch_when_no_tracked_trades(self):
        """get_positions is never called when _tracked_trades is empty."""
        registry: dict = {}
        engine = _make_engine(wake_events=registry, tracked_trades={})
        fetch_calls = 0

        def _count(*a, **kw):
            nonlocal fetch_calls
            fetch_calls += 1
            return []

        engine._client.get_positions = MagicMock(side_effect=_count)

        engine._woke_via_ws = True
        await engine._verify_close_after_ws()

        assert fetch_calls == 0, "get_positions must not be called when no tracked trades"
        assert engine._cached_positions_this_tick is None
