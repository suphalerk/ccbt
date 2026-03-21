#!/usr/bin/env python3
"""
generate_configs.py — Auto-generate config JSON files and docker-compose entries
for verified winning coins loaded from data/verified_winners.json.

Usage:
    python scripts/generate_configs.py
    python scripts/generate_configs.py --dry-run

Input:  data/verified_winners.json
Output: config_{prefix}_{strategy}.json files + updated docker-compose.yml

verified_winners.json schema:
[
  {
    "coin": "AAVE",
    "symbol": "AAVEUSDT",
    "strategy": "ema",           // "ema" or "ichi"
    "pf": 1.38,
    "trades_per_year": 14,
    "sharpe": 0.95,
    "max_dd_pct": 8.0,
    "params": {
      "ema_slope_min": 0.02,     // EMA only
      "atr_sl_mult": 1.0,        // both
      "atr_tp_mult": 3.0,        // both
      "atr_trail_mult": 2.0,     // both
      "volume_mult": 1.3,        // both
      "low_volume": false        // true = higher slippage (< $50M daily vol)
    },
    "funding_helps": false       // EMA only: enable funding scorer
  }
]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
WINNERS_FILE = REPO_ROOT / "data" / "verified_winners.json"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
EMA_TEMPLATE_FILE = REPO_ROOT / "config_doge.json"
ICHI_TEMPLATE_FILE = REPO_ROOT / "config_avax_ichi.json"

# Reduced risk for mass-portfolio single-bot configs
MASS_PORTFOLIO_RISK = 0.01          # 1% risk per trade
HIGH_SLIPPAGE_RATE = 0.0003         # Low-volume coins (< $50M daily)
STANDARD_SLIPPAGE_RATE = 0.00015    # Standard slippage

# Marker used to locate the dashboard service in docker-compose.yml
DASHBOARD_SERVICE_MARKER = "  # ---------------------------------------------------------------------------\n  # Dashboard Service"


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

def load_template(strategy: str) -> dict[str, Any]:
    """Load EMA or Ichimoku base config template from disk."""
    if strategy == "ema":
        path = EMA_TEMPLATE_FILE
    elif strategy == "ichi":
        path = ICHI_TEMPLATE_FILE
    else:
        raise ValueError(f"Unknown strategy type: {strategy!r}. Must be 'ema' or 'ichi'.")

    with path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def generate_ema_config(winner: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Build an EMA 15m config from template + winner params."""
    cfg = copy.deepcopy(template)
    params = winner.get("params", {})
    coin = winner["coin"]
    pf = winner["pf"]
    trades = winner["trades_per_year"]

    cfg["_comment"] = (
        f"{coin} EMA 15m — auto-generated from mass sweep, "
        f"PF {pf:.2f}, {trades} trades/yr"
    )
    cfg["symbol"] = winner["symbol"]

    # Reduce risk for portfolio diversification
    cfg["risk_per_trade"] = MASS_PORTFOLIO_RISK

    # Apply per-coin params if present
    if "ema_slope_min" in params:
        cfg["ema_slope_min"] = params["ema_slope_min"]
    if "atr_sl_mult" in params:
        cfg["atr_sl_mult"] = params["atr_sl_mult"]
    if "atr_tp_mult" in params:
        cfg["atr_tp_mult"] = params["atr_tp_mult"]
    if "atr_trail_mult" in params:
        cfg["atr_trail_mult"] = params["atr_trail_mult"]
        cfg["atr_trail_mult_trending"] = params["atr_trail_mult"]
    if "volume_mult" in params:
        cfg["volume_mult"] = params["volume_mult"]

    # Slippage: higher for low-volume coins
    cfg["slippage_rate"] = (
        HIGH_SLIPPAGE_RATE if params.get("low_volume", False)
        else STANDARD_SLIPPAGE_RATE
    )

    # Signal scorer: enable only if funding rate is known to help
    funding_helps: bool = winner.get("funding_helps", False)
    if "signal_scorer" not in cfg:
        cfg["signal_scorer"] = {}
    cfg["signal_scorer"]["enabled"] = funding_helps

    return cfg


def generate_ichi_config(winner: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Build an Ichimoku 1H config from template + winner params."""
    cfg = copy.deepcopy(template)
    params = winner.get("params", {})
    coin = winner["coin"]
    pf = winner["pf"]
    trades = winner["trades_per_year"]

    cfg["_comment"] = (
        f"{coin} Ichimoku 1H — auto-generated from mass sweep, "
        f"PF {pf:.2f}, {trades} trades/yr"
    )
    cfg["symbol"] = winner["symbol"]

    # Reduce risk for portfolio diversification
    cfg["risk_per_trade"] = MASS_PORTFOLIO_RISK

    # Apply per-coin params if present
    if "atr_sl_mult" in params:
        cfg["atr_sl_mult"] = params["atr_sl_mult"]
    if "atr_tp_mult" in params:
        cfg["atr_tp_mult"] = params["atr_tp_mult"]
    if "atr_trail_mult" in params:
        cfg["atr_trail_mult"] = params["atr_trail_mult"]
        cfg["atr_trail_mult_trending"] = params["atr_trail_mult"]
    if "volume_mult" in params:
        cfg["volume_mult"] = params["volume_mult"]

    # Slippage
    cfg["slippage_rate"] = (
        HIGH_SLIPPAGE_RATE if params.get("low_volume", False)
        else STANDARD_SLIPPAGE_RATE
    )

    return cfg


def build_config(winner: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Return (filename, config_dict) for a winner entry.

    Filename format: config_{prefix}_{strategy}.json
    where prefix = coin symbol in lower case (e.g. aaveusdt, ftmusdt).
    """
    strategy = winner["strategy"].lower()
    prefix = winner["symbol"].lower()   # e.g. "aaveusdt"
    filename = f"config_{prefix}_{strategy}.json"

    template = load_template(strategy)
    if strategy == "ema":
        cfg = generate_ema_config(winner, template)
    else:
        cfg = generate_ichi_config(winner, template)

    return filename, cfg


# ---------------------------------------------------------------------------
# docker-compose entry generation
# ---------------------------------------------------------------------------

def build_compose_service(winner: dict[str, Any], config_filename: str) -> str:
    """Return a YAML block string for one bot service."""
    strategy = winner["strategy"].lower()
    coin = winner["coin"].lower()
    # Service name uses coin name only; add strategy suffix if ambiguous
    service_name = f"bot-{coin}-{strategy}"
    container_name = f"tradingbot-{coin}-{strategy}"

    # YOLO_MODE for EMA bots (high leverage), not for Ichimoku
    yolo_line = "      - YOLO_MODE=1\n" if strategy == "ema" else ""

    return (
        f"  # ---------------------------------------------------------------------------\n"
        f"  # {winner['coin']} {'EMA 15m' if strategy == 'ema' else 'Ichimoku 1H'} Bot"
        f"  (PF {winner['pf']:.2f}, {winner['trades_per_year']} tr/yr)\n"
        f"  # ---------------------------------------------------------------------------\n"
        f"  {service_name}:\n"
        f"    build:\n"
        f"      context: .\n"
        f"      dockerfile: Dockerfile\n"
        f"    container_name: {container_name}\n"
        f"    restart: on-failure:5\n"
        f"    env_file:\n"
        f"      - .env\n"
        f"    environment:\n"
        f"      - CONFIG_FILE={config_filename}\n"
        f"{yolo_line}"
        f"    volumes:\n"
        f"      - bot-data:/app/data\n"
        f"      - ./{config_filename}:/app/{config_filename}:ro\n"
        f"    deploy:\n"
        f"      resources:\n"
        f"        limits:\n"
        f"          memory: 512M\n"
        f'          cpus: "0.5"\n'
        f"        reservations:\n"
        f"          memory: 128M\n"
        f'          cpus: "0.1"\n'
        f"    logging:\n"
        f"      driver: json-file\n"
        f"      options:\n"
        f'        max-size: "10m"\n'
        f'        max-file: "5"\n'
        f"\n"
    )


# ---------------------------------------------------------------------------
# docker-compose.yml update
# ---------------------------------------------------------------------------

def update_docker_compose(
    new_services: list[tuple[str, str, dict[str, Any]]],
    dry_run: bool,
) -> tuple[int, int]:
    """
    Insert new bot services into docker-compose.yml before the dashboard section.
    Also adds volume mounts for new configs in the dashboard service.

    Returns (existing_service_count, total_service_count).
    """
    with DOCKER_COMPOSE.open() as f:
        original = f.read()

    lines = original.splitlines(keepends=True)

    # -----------------------------------------------------------------------
    # Count existing bot services (lines starting with "  bot")
    # -----------------------------------------------------------------------
    existing_service_names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        # Service name lines look like "  bot:" or "  bot-doge:" (2-space indent, ends with colon)
        if line.startswith("  ") and not line.startswith("   ") and stripped.endswith(":"):
            svc = stripped.rstrip(":")
            if svc.startswith("bot") or svc in ("dashboard",):
                existing_service_names.add(svc)

    n_existing = len(existing_service_names)

    # -----------------------------------------------------------------------
    # Determine which services are genuinely new (not already in file)
    # -----------------------------------------------------------------------
    entries_to_insert: list[str] = []
    new_volume_mounts: list[str] = []
    added_count = 0

    for filename, service_yaml, winner in new_services:
        strategy = winner["strategy"].lower()
        coin = winner["coin"].lower()
        service_name = f"bot-{coin}-{strategy}"
        if service_name in existing_service_names:
            continue  # Already present — skip silently
        entries_to_insert.append(service_yaml)
        new_volume_mounts.append(f"      - ./{filename}:/app/{filename}:ro\n")
        added_count += 1

    if not entries_to_insert and not new_volume_mounts:
        return n_existing, n_existing

    # -----------------------------------------------------------------------
    # Find insertion point: line that starts the dashboard service comment
    # -----------------------------------------------------------------------
    dashboard_start_idx: int | None = None
    for i, line in enumerate(lines):
        if line == "  # ---------------------------------------------------------------------------\n":
            # Peek at next line to see if it mentions "Dashboard Service"
            if i + 1 < len(lines) and "Dashboard Service" in lines[i + 1]:
                dashboard_start_idx = i
                break

    if dashboard_start_idx is None:
        raise RuntimeError(
            "Could not locate '# Dashboard Service' section in docker-compose.yml. "
            "Cannot insert new services safely."
        )

    # -----------------------------------------------------------------------
    # Insert new bot services before dashboard
    # -----------------------------------------------------------------------
    new_lines = (
        lines[:dashboard_start_idx]
        + [yaml_block for yaml_block in entries_to_insert]
        + lines[dashboard_start_idx:]
    )

    # -----------------------------------------------------------------------
    # Add volume mounts to dashboard service
    # -----------------------------------------------------------------------
    if new_volume_mounts:
        updated_lines: list[str] = []
        in_dashboard_volumes = False
        last_dashboard_volume_idx: int | None = None

        # We rebuild the list, finding the last volume mount in the dashboard block
        # Pattern: after "  dashboard:" we look for "    volumes:" then track indented mounts
        dashboard_svc_found = False
        volumes_section_found = False
        i = 0
        while i < len(new_lines):
            line = new_lines[i]
            if not dashboard_svc_found and line.strip() == "dashboard:":
                dashboard_svc_found = True
            if dashboard_svc_found and not volumes_section_found and line.strip() == "volumes:":
                volumes_section_found = True
            if dashboard_svc_found and volumes_section_found:
                # A volume mount line looks like "      - ./something:/app/something:ro"
                if line.startswith("      - ") and ":/app/" in line:
                    last_dashboard_volume_idx = i
            updated_lines.append(line)
            i += 1

        if last_dashboard_volume_idx is not None:
            # Insert new volume mounts after the last existing one
            insert_at = last_dashboard_volume_idx + 1
            updated_lines = (
                updated_lines[:insert_at]
                + new_volume_mounts
                + updated_lines[insert_at:]
            )
        else:
            # Fallback: could not locate dashboard volumes — warn but don't crash
            print(
                "WARNING: Could not locate dashboard volumes section. "
                "New config mounts were NOT added to dashboard service.",
                file=sys.stderr,
            )
        new_lines = updated_lines

    updated_content = "".join(new_lines)
    total_services = n_existing + added_count

    if not dry_run:
        with DOCKER_COMPOSE.open("w") as f:
            f.write(updated_content)

    return n_existing, total_services


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _strategy_label(strategy: str) -> str:
    return "EMA 15m" if strategy == "ema" else "Ichimoku 1H"


def print_summary(
    generated: list[tuple[str, dict[str, Any], dict[str, Any]]],
    n_existing: int,
    total: int,
    dry_run: bool,
) -> None:
    label = "[DRY RUN] " if dry_run else ""
    print(f"\n{label}Generated configs:")
    total_trades = 0
    for filename, cfg, winner in generated:
        pf = winner["pf"]
        trades = winner["trades_per_year"]
        strategy_label = _strategy_label(winner["strategy"])
        coin = winner["coin"]
        total_trades += trades
        print(f"  {filename:<40} — {coin} {strategy_label} (PF {pf:.2f}, {trades} tr/yr)")

    new_count = total - n_existing
    print(
        f"\n{label}Updated docker-compose.yml: {total} bot services "
        f"({n_existing} existing + {new_count} new)"
    )
    print(
        f"Total portfolio: ~{total_trades} trades/yr, "
        f"1% risk per trade, max 8 concurrent positions"
    )
    if dry_run:
        print("\n(No files written — dry run mode)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate config files and docker-compose entries for verified winners."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing any files.",
    )
    parser.add_argument(
        "--winners-file",
        default=str(WINNERS_FILE),
        help=f"Path to verified_winners.json (default: {WINNERS_FILE})",
    )
    args = parser.parse_args()

    winners_path = Path(args.winners_file)
    if not winners_path.exists():
        print(
            f"ERROR: Winners file not found: {winners_path}\n"
            "Create data/verified_winners.json first.\n\n"
            "Expected schema:\n"
            "[\n"
            '  {\n'
            '    "coin": "AAVE",\n'
            '    "symbol": "AAVEUSDT",\n'
            '    "strategy": "ema",\n'
            '    "pf": 1.38,\n'
            '    "trades_per_year": 14,\n'
            '    "params": {\n'
            '      "ema_slope_min": 0.02,\n'
            '      "atr_sl_mult": 1.0,\n'
            '      "atr_tp_mult": 3.0,\n'
            '      "atr_trail_mult": 2.0,\n'
            '      "volume_mult": 1.3,\n'
            '      "low_volume": false\n'
            "    },\n"
            '    "funding_helps": false\n'
            "  }\n"
            "]",
            file=sys.stderr,
        )
        sys.exit(1)

    with winners_path.open() as f:
        winners: list[dict[str, Any]] = json.load(f)

    if not isinstance(winners, list) or not winners:
        print("ERROR: verified_winners.json must be a non-empty JSON array.", file=sys.stderr)
        sys.exit(1)

    # Normalize fields from verify_winners.py output format
    normalized: list[dict[str, Any]] = []
    for i, w in enumerate(winners):
        # Skip failed coins
        if "passed" in w and not w["passed"]:
            continue

        entry = dict(w)

        # Map engine_pf → pf
        if "pf" not in entry and "engine_pf" in entry:
            entry["pf"] = entry["engine_pf"]

        # Map prefix → symbol (e.g. "avaxusdt" → "AVAXUSDT")
        if "symbol" not in entry and "prefix" in entry:
            entry["symbol"] = entry["prefix"].upper()

        # Map strategy names: "Ichimoku"/"Ichi+Trail" → "ichi", "EMA(9/21)-*" → "ema"
        strat = entry.get("strategy", "")
        if strat.startswith("Ichi") or strat.startswith("ichi") or strat == "ichimoku_1h":
            entry["strategy"] = "ichi"
        elif strat.startswith("EMA") or strat.startswith("ema"):
            entry["strategy"] = "ema"

        # Map engine_trades → trades_per_year (approximate from 2yr data)
        if "trades_per_year" not in entry:
            total_trades = entry.get("engine_trades", entry.get("trades", 0))
            # Assume ~2yr backtest period
            entry["trades_per_year"] = max(1, round(total_trades / 2))

        normalized.append(entry)

    # Deduplicate: keep best PF per (coin, strategy_type) pair
    # e.g. if both "Ichimoku" and "Ichi+Trail" pass for same coin, keep the one with higher PF
    best_per_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in normalized:
        key = (entry["coin"], entry["strategy"])
        if key not in best_per_key or entry.get("pf", 0) > best_per_key[key].get("pf", 0):
            best_per_key[key] = entry
    winners = list(best_per_key.values())

    if not winners:
        print("No passing winners found in verified_winners.json.", file=sys.stderr)
        sys.exit(1)

    # Validate required fields
    required_fields = {"coin", "symbol", "strategy", "pf", "trades_per_year"}
    for i, w in enumerate(winners):
        missing = required_fields - w.keys()
        if missing:
            print(
                f"ERROR: winners[{i}] is missing required fields: {missing}",
                file=sys.stderr,
            )
            sys.exit(1)
        if w["strategy"] not in ("ema", "ichi"):
            print(
                f"ERROR: winners[{i}] strategy must be 'ema' or 'ichi', "
                f"got {w['strategy']!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build configs
    generated: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    compose_entries: list[tuple[str, str, dict[str, Any]]] = []

    for winner in winners:
        filename, cfg = build_config(winner)
        service_yaml = build_compose_service(winner, filename)

        generated.append((filename, cfg, winner))
        compose_entries.append((filename, service_yaml, winner))

        if not args.dry_run:
            out_path = REPO_ROOT / filename
            with out_path.open("w") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")  # trailing newline

    # Update docker-compose.yml
    n_existing, total = update_docker_compose(compose_entries, dry_run=args.dry_run)

    print_summary(generated, n_existing, total, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
