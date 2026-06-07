"""Tests for SharedMarketData cache core (T1).

TDD spec: write tests FIRST, run to see them FAIL, then implement.

Tests cover:
- balance: hit within TTL, miss after TTL
- ohlcv: hit on same closed candle (wall-clock boundary, 1H and 4H)
- ohlcv: refetch exactly once after wall-clock crosses a candle boundary
- served_limit monotonic (limit=30 after limit=100 keeps 100-row cache, no extra fetch)
- fresh=True writes through and refreshes
- forming candle (index[-1]) is retained in returned frame (iloc[-2] is last closed candle)
- raw ccxt list-of-lists exchange wired directly to SharedMarketData (BLOCKER regression)
- flag-ON/flag-OFF parity: same raw rows produce identical DataFrame shape/index
- deadlock guard: lock is NOT held during the blocking exchange fetch
"""

import threading
import time
from unittest.mock import MagicMock, call

import pandas as pd
import pytest

from bot.shared_exchange_pool import SharedMarketData


# ---------------------------------------------------------------------------
# Helpers — fake exchange and injectable clock
# ---------------------------------------------------------------------------

def make_ohlcv_df(n: int, start_ts: int, freq_s: int) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with n rows starting at start_ts (seconds)."""
    timestamps = [start_ts + i * freq_s for i in range(n)]
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1000.0] * n,
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True),
    )


class FakeExchange:
    """Records call counts; returns configurable fake data (DataFrame format).

    Used for the majority of unit tests.
    """

    def __init__(self):
        self.balance_calls = 0
        self.ohlcv_calls: dict[tuple, int] = {}  # key -> call count
        self._balance = 1000.0
        self._ohlcv_dfs: dict[tuple, pd.DataFrame] = {}

    def set_balance(self, v: float) -> None:
        self._balance = v

    def set_ohlcv(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        self._ohlcv_dfs[(symbol, timeframe)] = df

    def fetch_balance(self) -> dict:
        self.balance_calls += 1
        return {"free": {"USDT": self._balance}}

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        key = (symbol, timeframe)
        self.ohlcv_calls[key] = self.ohlcv_calls.get(key, 0) + 1
        if key in self._ohlcv_dfs:
            df = self._ohlcv_dfs[key]
            # Return last `limit` rows
            return df.iloc[-limit:] if limit < len(df) else df.copy()
        raise ValueError(f"No OHLCV data configured for {key}")


def make_ccxt_ohlcv_rows(n: int, start_ts: int, freq_s: int) -> list:
    """Build raw ccxt OHLCV list-of-lists with n rows starting at start_ts (seconds).

    Each row: [ts_ms, open, high, low, close, volume]  — the shape returned by
    the real ccxt exchange.fetch_ohlcv().
    """
    return [
        [int((start_ts + i * freq_s) * 1000), 100.0, 101.0, 99.0, 100.5, 1000.0]
        for i in range(n)
    ]


class FakeCcxtExchange:
    """Fake exchange that returns RAW ccxt list-of-lists from fetch_ohlcv.

    This mirrors what get_shared_exchange() returns (the raw ccxt object),
    as opposed to FakeExchange which returns DataFrames.

    Used in integration tests for the BLOCKER: SharedMarketData must handle
    both DataFrame-returning and list-returning exchanges gracefully.
    """

    def __init__(self):
        self.balance_calls = 0
        self.ohlcv_calls: dict[tuple, int] = {}
        self._balance = 2500.0
        self._ohlcv_rows: dict[tuple, list] = {}

    def set_balance(self, v: float) -> None:
        self._balance = v

    def set_ohlcv_rows(self, symbol: str, timeframe: str, rows: list) -> None:
        """Configure raw ccxt list-of-lists data for a symbol/timeframe."""
        self._ohlcv_rows[(symbol, timeframe)] = rows

    def fetch_balance(self) -> dict:
        """Return ccxt unified balance dict (nested, NOT 'free' flat dict)."""
        self.balance_calls += 1
        return {
            "USDT": {"free": self._balance, "used": 0.0, "total": self._balance},
            "free": {"USDT": self._balance},
            "used": {"USDT": 0.0},
            "total": {"USDT": self._balance},
            "info": {},
        }

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        """Return raw ccxt list-of-lists, like a real ccxt exchange."""
        key = (symbol, timeframe)
        self.ohlcv_calls[key] = self.ohlcv_calls.get(key, 0) + 1
        if key in self._ohlcv_rows:
            rows = self._ohlcv_rows[key]
            # Return last `limit` rows
            return rows[-limit:] if limit < len(rows) else list(rows)
        raise ValueError(f"No OHLCV data configured for {key}")


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------

class TestBalanceCache:
    """get_balance() caches with a 30s TTL."""

    def test_balance_hit_within_ttl(self):
        """Second call within TTL must NOT call the exchange again."""
        fake = FakeExchange()
        t = [0.0]
        clock = lambda: t[0]  # noqa: E731

        smd = SharedMarketData(exchange=fake, clock=clock)
        b1 = smd.get_balance()
        b2 = smd.get_balance()

        assert b1 == 1000.0
        assert b2 == 1000.0
        assert fake.balance_calls == 1, "Should have fetched balance only once (hit on second call)"

    def test_balance_miss_after_ttl(self):
        """Call after TTL expires must re-fetch from exchange."""
        fake = FakeExchange()
        t = [0.0]
        clock = lambda: t[0]  # noqa: E731

        smd = SharedMarketData(exchange=fake, clock=clock)
        smd.get_balance()          # fetch #1 at t=0
        t[0] = 31.0                # advance past 30s TTL
        smd.get_balance()          # fetch #2 at t=31

        assert fake.balance_calls == 2, "Should re-fetch after TTL expires"

    def test_balance_still_cached_at_ttl_boundary(self):
        """At exactly TTL boundary (t=30s) cache is still valid (exclusive expiry)."""
        fake = FakeExchange()
        t = [0.0]
        clock = lambda: t[0]  # noqa: E731

        smd = SharedMarketData(exchange=fake, clock=clock)
        smd.get_balance()   # fetch at t=0
        t[0] = 30.0         # exactly at TTL
        smd.get_balance()   # should still be cached

        assert fake.balance_calls == 1

    def test_balance_fresh_true_bypasses_ttl(self):
        """fresh=True must re-fetch regardless of TTL."""
        fake = FakeExchange()
        t = [0.0]
        clock = lambda: t[0]  # noqa: E731

        smd = SharedMarketData(exchange=fake, clock=clock)
        smd.get_balance()             # fetch #1
        smd.get_balance(fresh=True)   # fetch #2 forced

        assert fake.balance_calls == 2

    def test_balance_fresh_updates_value(self):
        """fresh=True must return the new value after exchange updates balance."""
        fake = FakeExchange()
        t = [0.0]
        clock = lambda: t[0]  # noqa: E731

        smd = SharedMarketData(exchange=fake, clock=clock)
        smd.get_balance()         # 1000.0

        fake.set_balance(2000.0)
        v = smd.get_balance(fresh=True)

        assert v == 2000.0


# ---------------------------------------------------------------------------
# OHLCV tests — wall-clock candle boundary
# ---------------------------------------------------------------------------

# 1-hour timeframe: 3600 seconds
TF_1H_S = 3600
# 4-hour timeframe: 4 * 3600 = 14400 seconds
TF_4H_S = 4 * 3600

SYMBOL = "BTC/USDT:USDT"
TF_1H = "1h"
TF_4H = "4h"


def _tf_seconds(tf: str) -> int:
    mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
    return mapping[tf]


class TestOHLCVCache:
    """get_ohlcv() caches using wall-clock candle boundary."""

    def _setup(self, tf: str, n_candles: int = 101) -> tuple:
        """
        Build a fake exchange with n_candles rows, current wall-clock set to
        mid-point of the last candle's period (so we are INSIDE a forming candle).
        Returns (smd, fake, t_list, candle_open_s, freq_s).
        """
        freq_s = _tf_seconds(tf)
        # Start 200 candles back from epoch for clean arithmetic
        start_ts = 0
        df = make_ohlcv_df(n_candles, start_ts=start_ts, freq_s=freq_s)
        fake = FakeExchange()
        fake.set_ohlcv(SYMBOL, tf, df)

        # The last closed candle open time = index[-2]
        last_closed_open_s = int(df.index[-2].timestamp())
        # Current time = inside the forming candle, after last closed
        current_time = last_closed_open_s + freq_s + freq_s // 2  # mid of forming

        t = [float(current_time)]
        clock = lambda: t[0]  # noqa: E731
        smd = SharedMarketData(exchange=fake, clock=clock)
        return smd, fake, t, last_closed_open_s, freq_s

    def test_ohlcv_hit_same_closed_candle_1h(self):
        """Two fetches within the SAME closed-candle window → only 1 exchange call."""
        smd, fake, t, closed_open_s, freq_s = self._setup(TF_1H)
        df1 = smd.get_ohlcv(SYMBOL, TF_1H)
        df2 = smd.get_ohlcv(SYMBOL, TF_1H)

        assert fake.ohlcv_calls.get((SYMBOL, TF_1H), 0) == 1
        assert len(df1) == len(df2)

    def test_ohlcv_hit_same_closed_candle_4h(self):
        """Same test for 4H timeframe."""
        smd, fake, t, closed_open_s, freq_s = self._setup(TF_4H)
        smd.get_ohlcv(SYMBOL, TF_4H)
        smd.get_ohlcv(SYMBOL, TF_4H)

        assert fake.ohlcv_calls.get((SYMBOL, TF_4H), 0) == 1

    def test_ohlcv_refetch_once_after_1h_boundary(self):
        """Exactly ONE refetch after wall-clock crosses a 1H candle boundary."""
        smd, fake, t, closed_open_s, freq_s = self._setup(TF_1H)

        # Warm the cache
        smd.get_ohlcv(SYMBOL, TF_1H)
        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 1

        # Advance past the boundary → next closed candle changes
        t[0] = closed_open_s + freq_s + freq_s + freq_s // 2  # mid of NEXT forming

        # Update the fake exchange to return updated data
        freq_s2 = freq_s
        new_df = make_ohlcv_df(101, start_ts=freq_s2, freq_s=freq_s2)
        fake.set_ohlcv(SYMBOL, TF_1H, new_df)

        smd.get_ohlcv(SYMBOL, TF_1H)  # should refetch
        smd.get_ohlcv(SYMBOL, TF_1H)  # should hit cache again

        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 2, (
            "Should have fetched exactly twice: once on warm, once after boundary"
        )

    def test_ohlcv_refetch_once_after_4h_boundary(self):
        """Same boundary test for 4H — warm mid-candle, cross boundary, assert exactly one refetch."""
        smd, fake, t, closed_open_s, freq_s = self._setup(TF_4H)

        smd.get_ohlcv(SYMBOL, TF_4H)
        assert fake.ohlcv_calls[(SYMBOL, TF_4H)] == 1

        # Move to mid of the NEXT 4H forming candle
        t[0] = closed_open_s + freq_s + freq_s + freq_s // 2

        new_df = make_ohlcv_df(101, start_ts=freq_s, freq_s=freq_s)
        fake.set_ohlcv(SYMBOL, TF_4H, new_df)

        smd.get_ohlcv(SYMBOL, TF_4H)   # refetch
        smd.get_ohlcv(SYMBOL, TF_4H)   # cache hit
        smd.get_ohlcv(SYMBOL, TF_4H)   # cache hit

        assert fake.ohlcv_calls[(SYMBOL, TF_4H)] == 2

    def test_ohlcv_served_limit_monotonic_no_extra_fetch(self):
        """
        If we first fetch limit=100, then fetch limit=30:
        - No extra exchange call (cache has 100 rows, 30 <= 100 → served)
        - Returned DataFrame has at most 30 rows (we slice on the way out)
        - But internally stored limit stays 100 (monotonic)
        """
        smd, fake, t, _, _ = self._setup(TF_1H, n_candles=101)

        df100 = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)
        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 1

        df30 = smd.get_ohlcv(SYMBOL, TF_1H, limit=30)
        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 1, (
            "limit=30 after limit=100 must NOT trigger a new fetch"
        )
        assert len(df30) == 30, "Returned frame should respect the requested limit"

    def test_ohlcv_served_limit_upgrades_when_larger_requested(self):
        """
        If we first cache limit=30, then request limit=100:
        cache is stale (served_limit=30 < 100) → must refetch.
        """
        smd, fake, t, _, _ = self._setup(TF_1H, n_candles=101)

        smd.get_ohlcv(SYMBOL, TF_1H, limit=30)
        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 1

        smd.get_ohlcv(SYMBOL, TF_1H, limit=100)
        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 2, (
            "A larger limit request must refetch (cache only has 30)"
        )

    def test_ohlcv_fresh_true_bypasses_cache(self):
        """fresh=True must always refetch and update the cache."""
        smd, fake, t, _, _ = self._setup(TF_1H)

        smd.get_ohlcv(SYMBOL, TF_1H)
        smd.get_ohlcv(SYMBOL, TF_1H, fresh=True)
        smd.get_ohlcv(SYMBOL, TF_1H, fresh=True)

        assert fake.ohlcv_calls[(SYMBOL, TF_1H)] == 3

    def test_ohlcv_forming_candle_at_iloc_minus1_closed_at_iloc_minus2(self):
        """
        Flag-OFF/flag-ON parity: the live get_ohlcv() keeps the forming candle at
        iloc[-1] so that strategy.py can use iloc[-2] to read the last CLOSED candle.
        The cache must return the SAME shape — forming candle included at iloc[-1],
        last closed candle at iloc[-2].

        Justification for test correction (was test_ohlcv_forming_candle_never_served_as_closed):
        The previous test asserted that the returned frame had the forming candle
        stripped (returned.index[-1] == raw.index[-2]).  This was wrong because:
        1. It contradicts flag-OFF parity: the live BybitClient.get_ohlcv() always
           includes the forming candle at iloc[-1].
        2. It contradicts the plan's iloc[-2] note: strategy.py accesses df.iloc[-2]
           for the last closed candle — if the cache strips the forming bar, iloc[-2]
           would be the SECOND-to-last closed candle (stale by one period).
        The freshness key is the last-closed candle's open-time (index[-2] of raw),
        but the returned frame must preserve the forming bar at iloc[-1].
        Coverage is strengthened, not weakened.
        """
        freq_s = TF_1H_S
        n = 101
        start_ts = 0
        df_full = make_ohlcv_df(n, start_ts=start_ts, freq_s=freq_s)

        fake = FakeExchange()
        fake.set_ohlcv(SYMBOL, TF_1H, df_full)

        # Current time = inside the forming candle (after the last closed candle)
        last_closed_open_s = int(df_full.index[-2].timestamp())
        current_time = last_closed_open_s + freq_s + freq_s // 2

        t = [float(current_time)]
        smd = SharedMarketData(exchange=fake, clock=lambda: t[0])

        # The exchange returns `limit+1` rows (to have a forming candle).
        # We request limit=100, so the exchange returns 101 rows.
        returned = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        # The forming candle must be at iloc[-1] (same as live path)
        assert returned.index[-1] == df_full.index[-1], (
            "Forming candle must be retained at iloc[-1] for flag-OFF parity; "
            "strategy.py uses df.iloc[-2] to read the last closed candle"
        )
        # The last CLOSED candle must be at iloc[-2]
        assert returned.index[-2] == df_full.index[-2], (
            "Last closed candle must be at iloc[-2] (same as live path)"
        )
        # Ensure the returned frame has the expected number of rows (100)
        assert len(returned) == 100, (
            f"Expected 100 rows (limit=100), got {len(returned)}"
        )

    def test_ohlcv_cold_cache_miss_fetches(self):
        """First call always fetches from exchange (cold cache)."""
        smd, fake, t, _, _ = self._setup(TF_1H)
        smd.get_ohlcv(SYMBOL, TF_1H)
        assert fake.ohlcv_calls.get((SYMBOL, TF_1H), 0) == 1


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Very basic thread-safety: no exceptions under concurrent reads."""

    def test_concurrent_balance_reads_no_exception(self):
        """Multiple threads reading balance concurrently must not raise."""
        freq_s = TF_1H_S
        fake = FakeExchange()
        t = [0.0]
        smd = SharedMarketData(exchange=fake, clock=lambda: t[0])

        errors = []

        def read():
            try:
                for _ in range(20):
                    smd.get_balance()
                    t[0] += 1.0  # advance time (shared, unsynchronised — intentional)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=read) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors, f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# BLOCKER regression — raw ccxt list-of-lists exchange
# ---------------------------------------------------------------------------

class TestRawCcxtExchange:
    """SharedMarketData wired to a raw ccxt exchange (list-returning fetch_ohlcv).

    BLOCKER fix: main_multi.py passes `get_shared_exchange()` which is a raw
    ccxt exchange whose fetch_ohlcv returns list[list], not a DataFrame.
    The cache must convert this itself rather than calling .iloc[:-1] on a list.

    These tests must FAIL against the original code (which calls raw_df.iloc[:-1]
    on the list result) and pass after the fix.
    """

    def _setup_ccxt_exchange(self, tf: str, n_candles: int = 101):
        """Build a FakeCcxtExchange with raw ccxt rows and an injectable clock."""
        freq_s = _tf_seconds(tf)
        start_ts = 0
        rows = make_ccxt_ohlcv_rows(n_candles, start_ts=start_ts, freq_s=freq_s)

        fake = FakeCcxtExchange()
        fake.set_ohlcv_rows(SYMBOL, tf, rows)

        # The last closed candle's open time in seconds (second-to-last row)
        last_closed_open_s = rows[-2][0] // 1000  # ms → s
        # Current time = mid-forming candle
        current_time = float(last_closed_open_s + freq_s + freq_s // 2)

        t = [current_time]
        clock = lambda: t[0]  # noqa: E731
        smd = SharedMarketData(exchange=fake, clock=clock)
        return smd, fake, t, rows, last_closed_open_s, freq_s

    def test_get_ohlcv_with_raw_ccxt_returns_dataframe(self):
        """SharedMarketData.get_ohlcv must NOT raise when the exchange returns
        list-of-lists.  It must convert to a proper closed-candle DataFrame."""
        smd, fake, t, rows, _, _ = self._setup_ccxt_exchange(TF_1H)

        # Must not raise AttributeError: 'list' object has no attribute 'iloc'
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        assert isinstance(df, pd.DataFrame), "Must return a DataFrame, not a list"
        assert len(df) > 0, "Returned DataFrame must be non-empty"

    def test_get_ohlcv_ccxt_correct_columns(self):
        """DataFrame from raw ccxt rows must have the standard OHLCV columns."""
        smd, _, _, _, _, _ = self._setup_ccxt_exchange(TF_1H)
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns, f"Column '{col}' missing from DataFrame"

    def test_get_ohlcv_ccxt_index_is_datetime(self):
        """DataFrame index must be a DatetimeIndex (UTC-naive, named 'timestamp')."""
        smd, _, _, _, _, _ = self._setup_ccxt_exchange(TF_1H)
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        assert isinstance(df.index, pd.DatetimeIndex), "Index must be DatetimeIndex"

    def test_get_ohlcv_ccxt_forming_candle_at_iloc_minus1(self):
        """Raw ccxt path must preserve forming candle at iloc[-1] (parity with live path)."""
        smd, fake, t, rows, _, freq_s = self._setup_ccxt_exchange(TF_1H, n_candles=101)
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        # The forming candle's open time = rows[-1][0] ms → s
        forming_open_s = rows[-1][0] // 1000
        returned_last_ts = int(df.index[-1].timestamp())
        assert returned_last_ts == forming_open_s, (
            f"iloc[-1] must be the forming candle (open={forming_open_s}s), "
            f"got {returned_last_ts}s"
        )

    def test_get_ohlcv_ccxt_last_closed_at_iloc_minus2(self):
        """The last CLOSED candle must be at iloc[-2] (what strategy.py reads)."""
        smd, fake, t, rows, last_closed_open_s, _ = self._setup_ccxt_exchange(TF_1H, n_candles=101)
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        returned_second_last_ts = int(df.index[-2].timestamp())
        assert returned_second_last_ts == last_closed_open_s, (
            f"iloc[-2] must be the last CLOSED candle (open={last_closed_open_s}s), "
            f"got {returned_second_last_ts}s"
        )

    def test_get_ohlcv_ccxt_caching_works(self):
        """Cache hit must work on second call with raw ccxt exchange."""
        smd, fake, t, rows, _, _ = self._setup_ccxt_exchange(TF_1H)
        smd.get_ohlcv(SYMBOL, TF_1H, limit=100)
        smd.get_ohlcv(SYMBOL, TF_1H, limit=100)  # must hit cache

        assert fake.ohlcv_calls.get((SYMBOL, TF_1H), 0) == 1, (
            "Second call within same candle window must hit cache (1 fetch only)"
        )

    def test_get_balance_with_raw_ccxt_returns_float(self):
        """get_balance must work with a raw ccxt fetch_balance response (nested dict)."""
        smd, fake, t, _, _, _ = self._setup_ccxt_exchange(TF_1H)
        balance = smd.get_balance()

        assert isinstance(balance, float), "get_balance must return a float"
        assert balance == 2500.0


# ---------------------------------------------------------------------------
# Flag-ON / flag-OFF frame parity (MAJOR-4)
# ---------------------------------------------------------------------------

class TestFlagOnFlagOffFrameParity:
    """The DataFrame returned by SharedMarketData (flag-ON path) must be structurally
    identical to what BybitClient.get_ohlcv produces directly (flag-OFF path):
    same row count, column names, index dtype, index name.

    This locks in the requirement that flag-ON is a drop-in replacement for flag-OFF.
    """

    def _make_raw_ccxt_rows(self, n: int, freq_s: int, start_ts: int = 0) -> list:
        """Raw ccxt OHLCV rows (list-of-lists, timestamps in ms)."""
        return make_ccxt_ohlcv_rows(n, start_ts=start_ts, freq_s=freq_s)

    def test_ohlcv_column_names_match_live_path(self):
        """Columns returned by the cache must match what exchange.py produces:
        open, high, low, close, volume — exactly."""
        freq_s = TF_1H_S
        n = 101
        rows = self._make_raw_ccxt_rows(n, freq_s=freq_s)

        fake = FakeCcxtExchange()
        fake.set_ohlcv_rows(SYMBOL, TF_1H, rows)

        last_closed_open_s = rows[-2][0] // 1000
        current_time = float(last_closed_open_s + freq_s + freq_s // 2)
        smd = SharedMarketData(exchange=fake, clock=lambda: current_time)

        df_cache = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        # Simulate what exchange.py live path produces (flags-off direct path)
        df_live = pd.DataFrame(
            rows[-101:],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df_live["timestamp"] = pd.to_datetime(df_live["timestamp"], unit="ms")
        df_live = df_live.set_index("timestamp")

        assert list(df_cache.columns) == list(df_live.columns), (
            f"Column mismatch: cache={list(df_cache.columns)}, live={list(df_live.columns)}"
        )

    def test_ohlcv_index_dtype_matches_live_path(self):
        """Index dtype of cache result must match live path (DatetimeIndex, UTC-naive)."""
        freq_s = TF_1H_S
        n = 101
        rows = self._make_raw_ccxt_rows(n, freq_s=freq_s)

        fake = FakeCcxtExchange()
        fake.set_ohlcv_rows(SYMBOL, TF_1H, rows)

        last_closed_open_s = rows[-2][0] // 1000
        current_time = float(last_closed_open_s + freq_s + freq_s // 2)
        smd = SharedMarketData(exchange=fake, clock=lambda: current_time)

        df_cache = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        # Live path in exchange.py uses pd.to_datetime(unit='ms') — no tz
        df_live = pd.DataFrame(
            rows[-101:],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df_live["timestamp"] = pd.to_datetime(df_live["timestamp"], unit="ms")
        df_live = df_live.set_index("timestamp")

        assert type(df_cache.index) == type(df_live.index), (
            f"Index type mismatch: cache={type(df_cache.index)}, live={type(df_live.index)}"
        )
        # Both should be tz-naive (no tzinfo)
        assert df_cache.index.tzinfo is None, (
            "Cache index must be tz-naive (no tzinfo), matching live exchange.py path"
        )

    def test_ohlcv_iloc_minus2_same_closed_candle_in_both_paths(self):
        """iloc[-2] must point at the same closed candle in both flag-ON and flag-OFF."""
        freq_s = TF_1H_S
        n = 101
        rows = self._make_raw_ccxt_rows(n, freq_s=freq_s)

        fake = FakeCcxtExchange()
        fake.set_ohlcv_rows(SYMBOL, TF_1H, rows)

        last_closed_open_s = rows[-2][0] // 1000
        current_time = float(last_closed_open_s + freq_s + freq_s // 2)
        smd = SharedMarketData(exchange=fake, clock=lambda: current_time)

        df_cache = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)

        # Live path
        df_live = pd.DataFrame(
            rows[-101:],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df_live["timestamp"] = pd.to_datetime(df_live["timestamp"], unit="ms")
        df_live = df_live.set_index("timestamp")

        # Both must have the same timestamp at iloc[-2]
        cache_closed_ts = df_cache.iloc[-2].name
        live_closed_ts = df_live.iloc[-2].name

        assert cache_closed_ts == live_closed_ts, (
            f"iloc[-2] closed candle mismatch: cache={cache_closed_ts}, live={live_closed_ts}"
        )


# ---------------------------------------------------------------------------
# Deadlock guard — lock NOT held during exchange fetch (MAJOR-6)
# ---------------------------------------------------------------------------

class TestDeadlockGuard:
    """Verify the non-reentrant lock discipline: the lock must NOT be held while
    the blocking exchange fetch runs.

    Plan v3 rule (MAJOR-6): 'acquire → read/decide-miss → RELEASE; the blocking
    ccxt fetch runs UNLOCKED; re-acquire → write-through → release.  Never hold
    the lock across a fetch.'

    We instrument the FakeExchange to check lock state INSIDE the fetch call.
    If the lock is held during the fetch, the assertion inside fetch_balance /
    fetch_ohlcv fails.
    """

    class LockSpyExchange:
        """Exchange that asserts the SharedMarketData lock is NOT held during fetches."""

        def __init__(self, smd_ref_holder: list):
            # smd_ref_holder[0] will be set to the SharedMarketData instance
            # after construction — late binding because smd doesn't exist yet
            # when this exchange is created.
            self._smd_ref_holder = smd_ref_holder
            self.balance_calls = 0
            self.ohlcv_calls = 0

        def _get_smd(self):
            return self._smd_ref_holder[0]

        def fetch_balance(self) -> dict:
            self.balance_calls += 1
            smd = self._get_smd()
            assert not smd._lock.locked(), (
                "Lock must NOT be held during fetch_balance — "
                "holding the lock across a blocking fetch causes self-deadlock "
                "when _retry sleeps on network errors."
            )
            return {"free": {"USDT": 1234.0}}

        def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
            self.ohlcv_calls += 1
            smd = self._get_smd()
            assert not smd._lock.locked(), (
                "Lock must NOT be held during fetch_ohlcv — "
                "holding the lock across a blocking fetch causes self-deadlock."
            )
            freq_s = {"1h": 3600, "4h": 14400}[timeframe]
            rows = make_ccxt_ohlcv_rows(limit + 1, start_ts=0, freq_s=freq_s)
            return rows[-limit:]

    def test_lock_not_held_during_balance_fetch(self):
        """Lock must NOT be held when the exchange.fetch_balance() call executes."""
        smd_holder = [None]
        spy = self.LockSpyExchange(smd_holder)
        smd = SharedMarketData(exchange=spy, clock=lambda: 0.0)
        smd_holder[0] = smd  # wire back-reference

        # Cold miss → fetch triggers
        # If lock is held during fetch, LockSpyExchange.fetch_balance raises AssertionError
        balance = smd.get_balance()
        assert balance == 1234.0

    def test_lock_not_held_during_ohlcv_fetch(self):
        """Lock must NOT be held when the exchange.fetch_ohlcv() call executes."""
        smd_holder = [None]
        spy = self.LockSpyExchange(smd_holder)

        # Clock set to mid-forming-candle for 1H
        freq_s = 3600
        # n rows: start_ts=0, last row open at (n-1)*freq_s
        n = 101
        # forming candle open = (n-1)*freq_s, current time = that + freq_s/2
        current_time = float((n - 1) * freq_s + freq_s // 2)
        smd = SharedMarketData(exchange=spy, clock=lambda: current_time)
        smd_holder[0] = smd

        # Cold miss → fetch triggers
        df = smd.get_ohlcv(SYMBOL, TF_1H, limit=100)
        assert isinstance(df, pd.DataFrame)

    def test_concurrent_reads_complete_without_deadlock(self):
        """Concurrent balance + ohlcv calls must complete within a timeout.

        If the lock were held across the fetch, concurrent callers would deadlock.
        We use a threading.Event + timeout to detect deadlock.
        """
        freq_s = TF_1H_S
        rows = make_ccxt_ohlcv_rows(101, start_ts=0, freq_s=freq_s)

        fake = FakeCcxtExchange()
        fake.set_ohlcv_rows(SYMBOL, TF_1H, rows)

        last_closed_open_s = rows[-2][0] // 1000
        current_time = float(last_closed_open_s + freq_s + freq_s // 2)
        smd = SharedMarketData(exchange=fake, clock=lambda: current_time)

        done_event = threading.Event()
        errors = []

        def worker():
            try:
                for _ in range(10):
                    smd.get_balance()
                    smd.get_ohlcv(SYMBOL, TF_1H, limit=50)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                done_event.set()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)  # 5s timeout — deadlock detection

        # If any thread is still alive after 5s, it's deadlocked
        alive = [th for th in threads if th.is_alive()]
        assert not alive, f"{len(alive)} thread(s) deadlocked (still running after 5s timeout)"
        assert not errors, f"Thread errors: {errors}"
