"""Telegram alert duration formatting (>=60 min -> 'X hr Y min')."""
import pytest
from bot.engine import _format_duration


@pytest.mark.parametrize("seconds,expected", [
    (0, "0 min"),
    (90, "1 min"),
    (2520, "42 min"),
    (3540, "59 min"),
    (3600, "1 hr"),
    (3660, "1 hr 1 min"),
    (7320, "2 hr 2 min"),     # 122 min
    (7200, "2 hr"),           # exact hours -> minutes dropped
    (None, "?"),
])
def test_format_duration(seconds, expected):
    assert _format_duration(seconds) == expected
