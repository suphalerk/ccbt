#!/usr/bin/env python3
"""Guard against timeframe drift — a config running a strategy on a timeframe
it was never research-validated on (the AO-1h-vs-4h class bug, 2026-06-07).

Source of truth: research/strategy_meta.json (signal_type -> validated_tf).
A config is checked by its ENABLED signal type vs its timeframe_signal.

Usage:
    python scripts/check_config_timeframes.py                 # scan all config_*.json
    python scripts/check_config_timeframes.py --deployed-only # only configs in start.sh
    python scripts/check_config_timeframes.py config_x.json   # specific files

Exit code: 0 = clean, 1 = drift found (non-deployed = warn), 2 = deployed drift (fail).
Generators can import validate_config() to reject a bad config before writing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
META_FILE = REPO / "research" / "strategy_meta.json"


def load_meta() -> dict:
    with open(META_FILE) as f:
        return json.load(f)["strategies"]


def _normalize_tf(tf):
    """Normalise OANDA/forex notation (H1/H4/M15) to ccxt style (1h/4h/15m)."""
    if not isinstance(tf, str):
        return tf
    alias = {"H1": "1h", "H4": "4h", "M15": "15m", "M5": "5m", "M1": "1m", "D": "1d"}
    return alias.get(tf, tf)


def enabled_signals(cfg: dict) -> list[str]:
    """Return the signal types enabled in a config's signals block."""
    return [
        k for k, v in cfg.get("signals", {}).items()
        if isinstance(v, dict) and v.get("enabled")
    ]


def validate_config(cfg: dict, meta: dict | None = None) -> list[str]:
    """Return a list of drift problems for one config (empty = OK).

    A problem is raised when an enabled signal type has a known validated_tf
    set and the config's timeframe_signal is not in it. Unknown signal types
    are skipped (reported separately by the CLI, never a hard failure).
    """
    if meta is None:
        meta = load_meta()
    tf = _normalize_tf(cfg.get("timeframe_signal"))
    problems: list[str] = []
    for sig in enabled_signals(cfg):
        entry = meta.get(sig)
        if entry is None:
            continue  # unknown strategy — not a drift failure
        valid = entry["validated_tf"]
        if tf not in valid:
            problems.append(
                f"{sig}: timeframe_signal={tf!r} not in validated_tf {valid} ({entry.get('note','')})"
            )
    return problems


def _deployed_set() -> set[str]:
    start_sh = REPO / "deploy" / "macos" / "start.sh"
    if not start_sh.exists():
        return set()
    text = start_sh.read_text()
    import re
    return set(re.findall(r"config_[a-z0-9_]+\.json", text))


def main(argv: list[str]) -> int:
    meta = load_meta()
    deployed_only = "--deployed-only" in argv
    files = [a for a in argv if a.endswith(".json")]
    if files:
        paths = [REPO / f for f in files]
    else:
        paths = sorted(REPO.glob("config_*.json"))

    deployed = _deployed_set()
    drift_deployed, drift_other, unknown = [], [], []

    for p in paths:
        try:
            cfg = json.loads(p.read_text())
        except Exception as e:
            print(f"  SKIP {p.name}: unreadable ({e})")
            continue
        is_dep = p.name in deployed
        if deployed_only and not is_dep:
            continue
        problems = validate_config(cfg, meta)
        for prob in problems:
            (drift_deployed if is_dep else drift_other).append(f"{p.name}: {prob}")
        for sig in enabled_signals(cfg):
            if sig not in meta:
                unknown.append(f"{p.name}: unknown signal {sig!r}")

    if drift_deployed:
        print(f"\n❌ DEPLOYED timeframe drift ({len(drift_deployed)}) — these trade with an unvalidated edge:")
        for d in drift_deployed:
            print(f"  {d}")
    if drift_other:
        print(f"\n⚠️  Non-deployed timeframe drift ({len(drift_other)}):")
        for d in drift_other:
            print(f"  {d}")
    if unknown:
        print(f"\nℹ️  Unknown signal types ({len(unknown)}) — add to strategy_meta.json:")
        for u in sorted(set(unknown)):
            print(f"  {u}")
    if not drift_deployed and not drift_other:
        print("✅ No timeframe drift found.")

    return 2 if drift_deployed else (1 if drift_other else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
