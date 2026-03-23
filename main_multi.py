"""Multi-bot runner — runs many trading bots in a single process.

Dramatically reduces RAM usage compared to 137 separate Docker containers:
  - Each Docker container: ~150-200 MB actual RAM, 512 MB limit
  - This process for 10-20 bots: ~200-350 MB total (shared ccxt exchange)

How it saves memory
-------------------
The largest per-bot cost is the ccxt exchange initialisation: ``load_markets()``
fetches all market symbols from the exchange and stores them in memory (~40-80 MB
per process).  When all bots run in ONE process, ``load_markets()`` is called
exactly ONCE and the resulting exchange object is shared via
``bot.shared_exchange_pool``.

Each bot still has its own independent:
  - ``RiskManager`` (per-symbol risk/drawdown state)
  - ``TradeJournal`` (per-symbol SQLite writes)
  - ``CalibrationTracker`` (per-symbol AI calibration)
  - ``asyncio.Event`` (shutdown signal)
  - Candle data cache (not shared — each symbol needs its own OHLCV)

Usage
-----
Run a glob pattern of config files::

    python main_multi.py --pattern "config_*ichi*.json"

Run explicit configs::

    python main_multi.py --configs config_avax_ichi.json config_near_ichi.json

Run a named group (defined at bottom of this file)::

    python main_multi.py --group ichimoku-1h

Run all config files in a directory::

    python main_multi.py --config-dir . --pattern "config_*.json"

Docker (replace 10 services with 1)::

    # docker-compose.yml
    bot-group-ichi-1h:
      command: python main_multi.py --group ichimoku-1h
      deploy:
        resources:
          limits:
            memory: 512M   # vs 512M × 14 = 7168M for separate containers
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from bot.engine import TradingEngine
from bot.logger import setup_logging
from bot.shared_exchange_pool import get_shared_exchange

load_dotenv()

logger = logging.getLogger(__name__)

REQUIRED_CONFIG_KEYS = [
    "symbol", "timeframe_signal", "timeframe_trend", "leverage",
    "risk_per_trade", "max_daily_loss", "max_positions",
    "ema_fast", "ema_slow", "ema_trend", "rsi_period",
    "rsi_min", "rsi_max", "atr_period", "atr_sl_mult", "atr_tp_mult",
]

# ---------------------------------------------------------------------------
# Predefined bot groups  (edit freely as the portfolio grows)
# ---------------------------------------------------------------------------

BOT_GROUPS: dict[str, list[str]] = {
    "ema-15m": [
        "config_arcusdt_ema.json",
        "config.json",
        "config_wif.json",
        "config_doge.json",
        "config_arb.json",
    ],
    "ichimoku-1h": [
        "config_avax_ichi.json",
        "config_near_ichi.json",
        "config_sol_ichi.json",
        "config_gunusdt_ichi.json",
        "config_berausdt_ichi.json",
        "config_athusdt_ichi.json",
        "config_zetausdt_ichi.json",
        "config_arcusdt_ichi.json",
        "config_animeusdt_ichi.json",
        "config_trumpusdt_ichi.json",
        "config_injusdt_ichi.json",
        "config_xlmusdt_ichi.json",
        "config_1000shibusdt_ichi.json",
        "config_trxusdt_ichi.json",
        "config_polusdt_ichi.json",
        "config_ipusdt_ichi.json",
    ],
    "ichimoku-4h": [
        "config_taousdt_ichi4h.json",
        "config_renderusdt_ichi4h.json",
        "config_hbarusdt_ichi4h.json",
        "config_arbusdt_ichi4h.json",
        "config_axsusdt_ichi4h.json",
        "config_husdt_ichi4h.json",
        "config_ondousdt_ichi4h.json",
        "config_signusdt_ichi4h.json",
        "config_tiausdt_ichi4h.json",
        "config_ltcusdt_ichist4h.json",
        "config_aptusdt_emaichi4h.json",
    ],
    "ichimoku-4h-trail": [
        "config_algousdt_ichi4htrail.json",
        "config_fetusdt_ichi4htrail.json",
        "config_polusdt_ichi4htrail.json",
        "config_polyxusdt_ichi4htrail.json",
        "config_saharausdt_ichi4htrail.json",
        "config_trxusdt_ichi4htrail.json",
        "config_xlmusdt_ichi4htrail.json",
    ],
    "supertrend": [
        "config_mstrusdt_supertrend.json",
        "config_xagusdt_supertrend.json",
        "config_saharausdt_supertrend.json",
    ],
    "volexp": [
        "config_1000pepeusdt_volexp.json",
        "config_wldusdt_volexp.json",
    ],
    "dualthrust": [
        "config_avaxusdt_dualthrust.json",
        "config_arcusdt_dualthrust.json",
        "config_1000bonkusdt_dualthrust.json",
        "config_aaveusdt_dualthrust.json",
        "config_aliceusdt_dualthrust.json",
        "config_ankrusdt_dualthrust.json",
        "config_aptusdt_dualthrust.json",
        "config_atomusdt_dualthrust.json",
        "config_axsusdt_dualthrust.json",
    ],
    "dualst": [
        "config_aktusdt_dualst4h.json",
        "config_enjusdt_dualst4h.json",
        "config_xplusdt_dualst4h.json",
        "config_xrpusdt_dualst4h.json",
        "config_humausdt_dualst.json",
        "config_lynusdt_dualst.json",
    ],
    "alligator": [
        "config_lightusdt_alligator.json",
        "config_linkusdt_alligator4h.json",
        "config_qntusdt_alligator4h.json",
        "config_suiusdt_alligator4h.json",
    ],
    "misc-new": [
        "config_opusdt_emaribbon.json",
        "config_1000bonkusdt_zscore.json",
        "config_1000shibusdt_emaribbon.json",
        "config_1000shibusdt_zscore.json",
        "config_adausdt_stochmtf.json",
        "config_arbusdt_stochmtf.json",
        "config_atomusdt_stochmtf.json",
        "config_axsusdt_stochmtf.json",
        "config_cfxusdt_stochmtf.json",
    ],
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: str) -> Optional[dict]:
    """Load and lightly validate a config file.

    Returns None (with a warning logged) if the file is missing or malformed
    so the multi-bot runner can skip bad configs without crashing.
    """
    try:
        with open(path) as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.warning("config_file_not_found", extra={"path": path})
        return None
    except json.JSONDecodeError as e:
        logger.warning("config_json_error", extra={"path": path, "error": str(e)})
        return None

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        logger.warning(
            "config_missing_keys", extra={"path": path, "missing": missing}
        )
        return None

    yolo_mode = os.getenv("YOLO_MODE", "").lower() in ("1", "true", "yes")
    max_leverage = 125 if yolo_mode else 25
    max_risk = 0.25 if yolo_mode else 0.1

    try:
        if not 1 <= config["leverage"] <= max_leverage:
            raise ValueError(f"leverage out of range: {config['leverage']}")
        if not 0.001 <= config["risk_per_trade"] <= max_risk:
            raise ValueError(f"risk_per_trade out of range: {config['risk_per_trade']}")
        if yolo_mode and not config.get("use_testnet", True):
            raise ValueError("YOLO mode requires use_testnet=true")
    except ValueError as e:
        logger.warning("config_validation_failed", extra={"path": path, "error": str(e)})
        return None

    return config


# ---------------------------------------------------------------------------
# Per-bot coroutine
# ---------------------------------------------------------------------------

async def run_bot(
    config_path: str,
    shared_exchange,
    shutdown_event: asyncio.Event,
) -> None:
    """Run a single bot coroutine inside the shared event loop.

    Args:
        config_path: Path to the bot's JSON config file.
        shared_exchange: Shared ccxt exchange instance (markets already loaded).
        shutdown_event: Global shutdown event — when set, all bots stop.
    """
    config = load_config(config_path)
    if config is None:
        logger.warning("bot_skipped_bad_config", extra={"config": config_path})
        return

    symbol = config.get("symbol", "unknown")
    logger.info("bot_starting", extra={"config": config_path, "symbol": symbol})

    try:
        engine = TradingEngine(
            config=config,
            shutdown_event=shutdown_event,
            shared_exchange=shared_exchange,
        )
        await engine.run()
    except Exception as e:
        logger.error(
            "bot_crashed",
            extra={"config": config_path, "symbol": symbol, "error": str(e)},
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_config_files(args) -> list[str]:
    """Resolve the final list of config files from CLI arguments.

    Precedence: --configs > --group > --pattern in --config-dir.
    Deduplicates while preserving order.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(path: str) -> None:
        abs_path = str(Path(path).resolve())
        if abs_path not in seen:
            seen.add(abs_path)
            result.append(abs_path)

    if args.configs:
        for p in args.configs:
            _add(p)
    elif args.group:
        group_name = args.group
        if group_name not in BOT_GROUPS:
            logger.error(
                "unknown_group",
                extra={"group": group_name, "available": list(BOT_GROUPS.keys())},
            )
            sys.exit(1)
        config_dir = Path(args.config_dir)
        for fname in BOT_GROUPS[group_name]:
            candidate = config_dir / fname
            _add(str(candidate))
    else:
        pattern = str(Path(args.config_dir) / args.pattern)
        for p in sorted(glob.glob(pattern)):
            _add(p)

    return result


def handle_shutdown(signum, frame, shutdown_event: asyncio.Event) -> None:
    """Handle OS shutdown signals by setting the shared event."""
    logger.info("shutdown_signal_received", extra={"signal": signum})
    # asyncio.Event.set() is not thread-safe from a signal handler, but
    # using call_soon_threadsafe is the correct approach.
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(shutdown_event.set)
    except RuntimeError:
        # No running loop yet (shouldn't happen in practice)
        shutdown_event.set()


async def async_main(config_files: list[str]) -> None:
    """Start one shared exchange pool, then run all bots concurrently."""
    if not config_files:
        logger.error("no_config_files_found")
        return

    # Determine exchange type from first valid config
    first_config: Optional[dict] = None
    for path in config_files:
        first_config = load_config(path)
        if first_config is not None:
            break

    if first_config is None:
        logger.error("all_configs_invalid")
        return

    exchange_name = first_config.get("exchange", "bybit").lower()
    use_testnet = first_config.get("use_testnet", True)

    logger.info(
        "multi_bot_starting",
        extra={
            "bot_count": len(config_files),
            "exchange": exchange_name,
            "testnet": use_testnet,
        },
    )

    # Build shared exchange ONCE (saves ~60 MB × N bots in memory)
    shared_exchange = get_shared_exchange(exchange_name, use_testnet)

    # Shared shutdown event — setting it stops all bots
    shutdown_event = asyncio.Event()

    # Register signal handlers now that we have the event loop
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda e=shutdown_event, s=sig: (
                logger.info("shutdown_signal_received", extra={"signal": s}),
                e.set(),
            ),
        )

    # Launch all bots as concurrent tasks
    tasks = [
        asyncio.create_task(
            run_bot(cfg, shared_exchange, shutdown_event),
            name=f"bot-{Path(cfg).stem}",
        )
        for cfg in config_files
    ]

    # Launch Telegram command handler alongside bots
    from bot.telegram_commands import run_telegram_handler
    telegram_task = asyncio.create_task(
        run_telegram_handler(shutdown_event),
        name="telegram-handler",
    )
    tasks.append(telegram_task)

    bot_count = len(tasks) - 1  # Exclude telegram handler
    logger.info("all_bots_launched", extra={"count": bot_count})

    # Single summary alert instead of 167 individual start messages
    from bot.telegram import send_alert
    send_alert(
        f"🟢 <b>CCBT Started</b>\n"
        f"Bots: {bot_count}\n"
        f"Exchange: {exchange_name}\n"
        f"Mode: {'testnet' if use_testnet else 'LIVE'}",
        silent=True,
    )

    # Wait for all bots to finish (they stop when shutdown_event is set)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for cfg, result in zip(config_files, results):
        if isinstance(result, Exception):
            logger.error(
                "bot_task_failed",
                extra={"config": cfg, "error": str(result)},
            )

    logger.info("multi_bot_runner_finished", extra={"bot_count": len(tasks)})

    send_alert(f"🔴 <b>CCBT Stopped</b>\nBots: {bot_count}", silent=True)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run multiple trading bots in a single process (RAM-efficient).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        metavar="CONFIG",
        help="Explicit list of config JSON files.",
    )
    parser.add_argument(
        "--group",
        metavar="GROUP",
        choices=list(BOT_GROUPS.keys()),
        help=f"Named group of bots. Choices: {', '.join(BOT_GROUPS.keys())}",
    )
    parser.add_argument(
        "--config-dir",
        default=".",
        metavar="DIR",
        help="Directory to scan for config files (default: current dir).",
    )
    parser.add_argument(
        "--pattern",
        default="config_*.json",
        metavar="GLOB",
        help='Glob pattern within --config-dir (default: "config_*.json").',
    )
    parser.add_argument(
        "--list-groups",
        action="store_true",
        help="Print all available group names and their configs, then exit.",
    )

    args = parser.parse_args()

    setup_logging()

    if args.list_groups:
        for name, configs in BOT_GROUPS.items():
            print(f"\n{name} ({len(configs)} bots):")
            for c in configs:
                print(f"  {c}")
        return

    config_files = resolve_config_files(args)
    if not config_files:
        logger.error("no_configs_resolved", extra={"args": vars(args)})
        sys.exit(1)

    logger.info("resolved_configs", extra={"count": len(config_files)})
    for p in config_files:
        logger.info("  config", extra={"path": p})

    asyncio.run(async_main(config_files))


if __name__ == "__main__":
    main()
