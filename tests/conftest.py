"""Shared pytest fixtures + global safety guards for the test suite.

HARD GUARD — no test may ever send a real Telegram message.

History: tests that drive the real engine close path (e.g.
test_close_reason_taxonomy.py's check_closed_positions integration tests) call
bot.engine.send_alert with fixture trades. When pytest ran in an environment that
had TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID set (e.g. the dev .env), those fixture
closes were delivered to the real Telegram chat — flooding it with bogus
"BUY closed (trail_stop/breakeven/...)" alerts at round fixture PnLs.

This autouse fixture neutralises Telegram for EVERY test regardless of whether
the individual test remembers to mock it:
  1. blanks bot.telegram._BOT_TOKEN / _CHAT_ID so send_alert early-returns
     (the engine + main_multi call the same function object, which reads these
     module globals at call time — so blanking them covers all callers);
  2. additionally no-ops the already-imported `send_alert` names in bot.engine
     and main_multi as belt-and-suspenders.
Tests that explicitly patch bot.engine.send_alert still work (their patch wins
within the test body).
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _block_real_telegram(monkeypatch):
    try:
        import bot.telegram as _tg
    except Exception:
        return  # telegram module not importable in this env — nothing to guard
    monkeypatch.setattr(_tg, "_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(_tg, "_CHAT_ID", "", raising=False)
    monkeypatch.setattr(_tg, "send_alert", lambda *a, **k: False, raising=False)
    # Neutralise the names already imported into other modules
    # (`from bot.telegram import send_alert` binds a separate reference).
    for _modname in ("bot.engine", "main_multi"):
        _mod = sys.modules.get(_modname)
        if _mod is not None and hasattr(_mod, "send_alert"):
            monkeypatch.setattr(_mod, "send_alert", lambda *a, **k: False, raising=False)
