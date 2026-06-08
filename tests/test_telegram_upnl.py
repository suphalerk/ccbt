"""Tests for the /upnl Telegram command in bot/telegram_commands.py.

Hand-computes expected totals independently of _cmd_upnl to avoid
tautological tests.
"""

import pytest
import bot.telegram_commands as tc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(symbol: str, side: str, contracts: float, upnl: float,
                   entry: float = 50000.0, mark: float = 51000.0) -> dict:
    """Build a minimal unified ccxt position dict."""
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "unrealizedPnl": upnl,
        "entryPrice": entry,
        "markPrice": mark,
        "info": {},
    }


# Hand-crafted test data used across multiple tests.
# Values are chosen so that the total (+17.25) does NOT equal any single
# position magnitude (30.00 or -12.75), making substring-collision impossible.
# LONG BTC:   contracts=0.1, upnl=+30.00
# SHORT ETH:  contracts=0.5, upnl=-12.75
# FLAT SOL:   contracts=0.0  → MUST be filtered out
_LONG_POS = _make_position("BTC/USDT:USDT", "long", 0.1, 30.00, 50000.0, 50300.0)
_SHORT_POS = _make_position("ETH/USDT:USDT", "short", 0.5, -12.75, 2000.0, 1974.5)
_FLAT_POS = _make_position("SOL/USDT:USDT", "long", 0.0, 0.0, 100.0, 100.0)

# Independently hand-computed expected total (not derived from _cmd_upnl):
_EXPECTED_TOTAL = 30.00 + (-12.75)  # = +17.25


class FakeExchange:
    """Synchronous fake exchange returning hand-built position list."""

    def __init__(self, positions):
        self._positions = positions

    def fetch_positions(self):
        return list(self._positions)


class ErrorExchange:
    """Fake exchange that raises on fetch_positions."""

    def fetch_positions(self):
        raise RuntimeError("network timeout")


# ---------------------------------------------------------------------------
# Fixture: reset _EXCHANGE after every test to prevent state leakage
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_exchange():
    """Ensure module _EXCHANGE is None before and after each test."""
    tc.set_exchange(None)
    yield
    tc.set_exchange(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCmdUpnlNoExchange:
    def test_no_exchange_returns_unavailable_message(self):
        """When _EXCHANGE is None, reply must mention unavailable — no crash."""
        result = tc._cmd_upnl()
        assert isinstance(result, str)
        # Should communicate that live uPnL is not available
        assert "unavailable" in result.lower() or "not connected" in result.lower()

    def test_no_exchange_does_not_raise(self):
        """_cmd_upnl must never raise even when exchange is None."""
        try:
            tc._cmd_upnl()
        except Exception as exc:
            pytest.fail(f"_cmd_upnl raised with None exchange: {exc}")


class TestCmdUpnlExchangeError:
    def test_fetch_error_returns_friendly_string(self):
        """When fetch_positions raises, reply is a friendly error — no propagation."""
        tc.set_exchange(ErrorExchange())
        result = tc._cmd_upnl()
        assert isinstance(result, str)
        # Should mention error without exposing a bare traceback
        assert "error" in result.lower() or "Error" in result

    def test_fetch_error_does_not_raise(self):
        """No exception must escape _cmd_upnl when fetch_positions raises."""
        tc.set_exchange(ErrorExchange())
        try:
            tc._cmd_upnl()
        except Exception as exc:
            pytest.fail(f"_cmd_upnl raised on fetch error: {exc}")


class TestCmdUpnlTotalCalculation:
    def test_total_equals_hand_summed_value(self):
        """Total uPnL line must display the independently hand-computed sum.

        _EXPECTED_TOTAL = +17.25, which equals neither per-position magnitude
        (30.00 or 12.75), so a bare substring match cannot accidentally pass
        because the value appears on a position line rather than the total line.
        """
        tc.set_exchange(FakeExchange([_LONG_POS, _SHORT_POS, _FLAT_POS]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        total_line = next((l for l in lines if "Total" in l), None)
        assert total_line is not None, f"No 'Total' line found in reply:\n{result}"
        # Total is positive: must show the '+' sign on the total line
        assert f"+{_EXPECTED_TOTAL:.2f}" in total_line, (
            f"Expected '+{_EXPECTED_TOTAL:.2f}' on Total line, got: {total_line!r}\n"
            f"Full reply:\n{result}"
        )
        # Guard: a wrong total (e.g. from a sum bug) must not silently pass
        wrong_total = "999.99"
        assert wrong_total not in total_line, (
            f"Total line unexpectedly contains {wrong_total!r}: {total_line!r}"
        )

    def test_total_sign_positive_when_net_positive(self):
        """Net positive total must display a 🟢 icon on the total line."""
        tc.set_exchange(FakeExchange([_LONG_POS, _SHORT_POS, _FLAT_POS]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        total_line = next((l for l in lines if "Total" in l), None)
        assert total_line is not None, "No total line found"
        assert "🟢" in total_line, f"Expected 🟢 on positive total line: {total_line}"

    def test_total_negative_when_net_negative(self):
        """When all positions are losing, total line must show negative value + 🔴."""
        # Single losing position — total is clearly negative, no collision risk
        losing_pos = _make_position("BTC/USDT:USDT", "long", 0.1, -40.00, 50000.0, 49600.0)
        tc.set_exchange(FakeExchange([losing_pos]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        total_line = next((l for l in lines if "Total" in l), None)
        assert total_line is not None, f"No 'Total' line found:\n{result}"
        assert "-40.00" in total_line, (
            f"Expected '-40.00' on negative total line, got: {total_line!r}"
        )
        assert "🔴" in total_line, f"Expected 🔴 on negative total line: {total_line}"


class TestCmdUpnlPositionFilter:
    def test_both_nonzero_symbols_appear(self):
        """BTC and ETH (nonzero contracts) must appear in the reply."""
        tc.set_exchange(FakeExchange([_LONG_POS, _SHORT_POS, _FLAT_POS]))
        result = tc._cmd_upnl()
        assert "BTC" in result, f"BTC missing from reply:\n{result}"
        assert "ETH" in result, f"ETH missing from reply:\n{result}"

    def test_zero_contract_symbol_excluded(self):
        """SOL (contracts=0) must NOT appear in the reply."""
        tc.set_exchange(FakeExchange([_LONG_POS, _SHORT_POS, _FLAT_POS]))
        result = tc._cmd_upnl()
        assert "SOL" not in result, f"SOL (flat) must be filtered out:\n{result}"

    def test_empty_positions_returns_no_open(self):
        """Exchange returning all-flat positions → 'No open positions.'"""
        tc.set_exchange(FakeExchange([_FLAT_POS]))
        result = tc._cmd_upnl()
        assert "No open positions" in result


class TestCmdUpnlPerPositionIcon:
    def test_positive_upnl_position_has_green_icon(self):
        """The LONG BTC position (upnl=+25.50) must show a 🟢 icon."""
        tc.set_exchange(FakeExchange([_LONG_POS]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        btc_line = next((l for l in lines if "BTC" in l), None)
        assert btc_line is not None, "BTC line not found"
        assert "🟢" in btc_line, f"Expected 🟢 on positive uPnL line: {btc_line}"

    def test_negative_upnl_position_has_red_icon(self):
        """The SHORT ETH position (upnl=-12.75) must show a 🔴 icon."""
        tc.set_exchange(FakeExchange([_SHORT_POS]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        eth_line = next((l for l in lines if "ETH" in l), None)
        assert eth_line is not None, "ETH line not found"
        assert "🔴" in eth_line, f"Expected 🔴 on negative uPnL line: {eth_line}"

    def test_icon_reflects_upnl_sign_not_side(self):
        """Icon must track the sign of uPnL, not the trade side.

        A SHORT position with positive uPnL (profitable) must show 🟢,
        not 🔴 (which would be 'short side color').
        """
        profitable_short = _make_position(
            "LINK/USDT:USDT", "short", 1.0, 8.00, 20.0, 19.2
        )
        tc.set_exchange(FakeExchange([profitable_short]))
        result = tc._cmd_upnl()
        lines = result.splitlines()
        link_line = next((l for l in lines if "LINK" in l), None)
        assert link_line is not None, "LINK line not found"
        assert "🟢" in link_line, (
            f"Profitable short must show 🟢 (based on uPnL sign, not side): {link_line}"
        )


class TestSetExchange:
    def test_set_exchange_stores_value(self):
        """set_exchange injects the exchange so _cmd_upnl can use it."""
        fake = FakeExchange([_LONG_POS])
        tc.set_exchange(fake)
        assert tc._EXCHANGE is fake

    def test_set_exchange_none_clears_value(self):
        """set_exchange(None) clears the stored instance."""
        tc.set_exchange(FakeExchange([]))
        tc.set_exchange(None)
        assert tc._EXCHANGE is None


class TestFmtPrice:
    """Unit tests for _fmt_price — no exchange needed."""

    def test_large_price_no_scientific_notation(self):
        """BTC-range prices must NOT produce scientific notation ('e+')."""
        result = tc._fmt_price(50255.0)
        assert "e+" not in result and "e-" not in result, (
            f"Scientific notation found for 50255.0: {result!r}"
        )

    def test_large_price_decimal_format(self):
        """BTC-range prices must display with comma-separated thousands and 2dp."""
        assert tc._fmt_price(50255.0) == "50,255.00"
        assert tc._fmt_price(50000.0) == "50,000.00"

    def test_mid_price_decimal_format(self):
        """ETH-range prices (1000-999) must show 4 decimal places, no comma."""
        result = tc._fmt_price(1974.5)
        assert "e+" not in result and "e-" not in result
        # 1974.5 >= 1000 → comma-separated 2dp
        assert result == "1,974.50"

    def test_sub_dollar_price_no_scientific_notation(self):
        """Sub-dollar prices must NOT produce scientific notation."""
        result = tc._fmt_price(0.00421)
        assert "e+" not in result and "e-" not in result, (
            f"Scientific notation found for 0.00421: {result!r}"
        )
        assert "0.004210" == result

    def test_upnl_reply_no_scientific_notation(self):
        """BTC entry→mark annotation in /upnl reply must not contain 'e+'."""
        btc_pos = _make_position("BTC/USDT:USDT", "long", 0.1, 255.0, 50000.0, 50255.0)
        tc.set_exchange(FakeExchange([btc_pos]))
        result = tc._cmd_upnl()
        assert "e+" not in result, (
            f"Scientific notation found in /upnl reply:\n{result}"
        )
        # Should show the mark price in plain decimal
        assert "50,255.00" in result, (
            f"Expected '50,255.00' in /upnl reply:\n{result}"
        )


class TestDispatchUpnl:
    def test_dispatch_upnl_registered(self):
        """/upnl must be reachable through the standard dispatcher."""
        tc.set_exchange(FakeExchange([_LONG_POS]))
        result = tc._dispatch("/upnl")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_help_contains_upnl(self):
        """/help text must list the /upnl command."""
        result = tc._cmd_help()
        assert "/upnl" in result
