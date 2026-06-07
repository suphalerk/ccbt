"""Tests for the timeframe-drift guard (scripts/check_config_timeframes.py)."""

import json
from pathlib import Path

import pytest

from scripts.check_config_timeframes import load_meta, validate_config, enabled_signals

META = load_meta()


def _cfg(signal: str, tf: str) -> dict:
    return {"timeframe_signal": tf, "signals": {signal: {"enabled": True}}}


def test_meta_loads_and_awesome_is_strict_4h():
    assert META["awesome_oscillator"]["validated_tf"] == ["4h"]


def test_awesome_on_1h_is_drift():
    # The exact bug this guard exists to catch.
    assert validate_config(_cfg("awesome_oscillator", "1h"), META)


def test_awesome_on_4h_is_clean():
    assert validate_config(_cfg("awesome_oscillator", "4h"), META) == []


def test_ema_crossover_only_15m():
    assert validate_config(_cfg("ema_crossover", "1h"), META)      # drift
    assert validate_config(_cfg("ema_crossover", "15m"), META) == []  # ok


def test_multi_tf_strategy_accepts_both():
    assert validate_config(_cfg("ichimoku_cloud", "1h"), META) == []
    assert validate_config(_cfg("ichimoku_cloud", "4h"), META) == []
    assert validate_config(_cfg("ichimoku_cloud", "15m"), META)   # drift


def test_unknown_signal_is_not_a_failure():
    assert validate_config(_cfg("some_new_signal", "1h"), META) == []


def test_enabled_signals_ignores_disabled():
    cfg = {"signals": {"a": {"enabled": True}, "b": {"enabled": False}}}
    assert enabled_signals(cfg) == ["a"]


def test_deployed_awesome_configs_are_4h():
    """Regression: every AO config still on disk that is DEPLOYED must be 4h."""
    repo = Path(__file__).resolve().parent.parent
    start_sh = (repo / "deploy" / "macos" / "start.sh").read_text()
    for p in repo.glob("config_*awesome*.json"):
        if p.name not in start_sh:
            continue  # only deployed ones must be clean
        cfg = json.loads(p.read_text())
        assert validate_config(cfg, META) == [], f"{p.name} deployed with TF drift"
