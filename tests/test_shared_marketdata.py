"""Tests for SharedMarketData cache core (T1).

TDD spec: write tests FIRST, run to see them FAIL, then implement.

Tests cover:
- balance: hit within TTL, miss after TTL
- ohlcv: hit on same closed candle (wall-clock boundary, 1H and 4H)
- ohlcv: refetch exactly once after wall-clock crosses a candle boundary
- served_limit monotonic (limit=30 after limit=100 keeps 100-row cache, no extra fetch)
- fresh=True writes through and refreshes
- forming candle (index[-1]) never served as the closed candle
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
    """Records call counts; returns configurable fake data."""

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

    def test_ohlcv_forming_candle_never_served_as_closed(self):
        """
        The forming candle (index[-1] of the fetched data) must never appear as iloc[-2]
        of the returned data — i.e. returned data has the forming candle stripped.

        We verify by checking that the returned DataFrame's last row has the same
        timestamp as the second-to-last row of the raw fetched data.
        """
        freq_s = TF_1H_S
        n = 101
        start_ts = 0
        df_full = make_ohlcv_df(n, start_ts=start_ts, freq_s=freq_s)

        fake = FakeExchange()
        fake.set_ohlcv(SYMBOL, TF_1H, df_full)

        # Current time = inside the forming candle
        last_closed_open_s = int(df_full.index[-2].timestamp())
        current_time = last_closed_open_s + freq_s + freq_s // 2

        t = [float(current_time)]
        smd = SharedMarketData(exchange=fake, clock=lambda: t[0])

        returned = smd.get_ohlcv(SYMBOL, TF_1H)
        # The returned df's last row must be the second-to-last of raw data (closed candle)
        # i.e. the forming candle must have been stripped
        assert returned.index[-1] == df_full.index[-2], (
            "Forming candle (index[-1] of raw data) must not be in the returned frame; "
            "last row should be the last CLOSED candle"
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
