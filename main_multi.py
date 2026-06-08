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
from bot.shared_exchange_pool import SharedMarketData, get_shared_exchange


# ---------------------------------------------------------------------------
# Global portfolio manager — enforces cross-bot position limits
# ---------------------------------------------------------------------------

class PortfolioManager:
    """Shared position tracker for all bots in a single process.

    Enforces two safety constraints across all bots:

    1. Global position cap — total open positions across all bots never
       exceeds ``max_positions``.
    2. Duplicate-coin gate — at most one bot at a time may hold a position
       in any given symbol.

    All methods that mutate state are protected by an ``asyncio.Lock`` so
    they are safe to call concurrently from many bot coroutines.
    """

    def __init__(self, max_positions: int = 10) -> None:
        self.max_positions = max_positions
        self._open_coins: set[str] = set()
        self._lock = asyncio.Lock()
        # Shared close-dedup registry passed to every TradingEngine.  When
        # multiple config-bots share one netted exchange position (e.g. AXS
        # has 7 bots), the first bot to detect the absence journals + alerts;
        # subsequent bots only remove their local trade_id (silent dedup).
        # Dict is keyed by normalised symbol, value is the close timestamp.
        self.recently_closed: dict = {}

    @property
    def open_count(self) -> int:
        # Count by UNIQUE coin — multiple strategy-bots share one netted
        # exchange position per symbol, so the set is the source of truth.
        # (A separate counter double-counted same-symbol register_open calls
        # on restart, inflating the global cap and blocking all new entries.)
        return len(self._open_coins)

    async def can_open(self, symbol: str) -> bool:
        """Return True if a new position may be opened for *symbol*.

        Checks both the global position cap and the duplicate-coin gate.
        Does NOT modify state — call :meth:`register_open` after the order
        is successfully placed.
        """
        async with self._lock:
            if len(self._open_coins) >= self.max_positions:
                return False
            if symbol in self._open_coins:
                return False
            return True

    async def register_open(self, symbol: str) -> None:
        """Record that a new position has been opened for *symbol*.

        Idempotent per symbol — registering the same coin twice (e.g. several
        strategy-bots restoring one netted exchange position on restart) does
        not inflate the count.
        """
        async with self._lock:
            self._open_coins.add(symbol)

    async def register_close(self, symbol: str) -> None:
        """Record that a position for *symbol* has been closed."""
        async with self._lock:
            self._open_coins.discard(symbol)

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
# Startup warm plan helper
# ---------------------------------------------------------------------------

def compute_warm_plan(configs: list[dict]) -> dict[tuple[str, str], int]:
    """Compute the OHLCV warm limit for every unique (symbol, timeframe) group.

    Each bot that references a ``(symbol, timeframe)`` pair contributes its
    ``ema_trend`` to that group.  The warm limit for the group is::

        max(100, max(ema_trend_i * 3) over bots in the group)

    The floor of 100 ensures the cache always holds enough bars for the
    default indicator warmup period.  The ``ema_trend * 3`` factor covers
    the longest EMA used by any bot in the group so the first signal can be
    computed without an extra fetch.

    A bot contributes two timeframe keys: ``timeframe_signal`` and
    ``timeframe_trend``.  When the two are identical the key is deduped
    (appears only once, as expected from a ``dict``).

    Args:
        configs: List of valid bot config dicts (each must have at least
            "symbol", "timeframe_signal", "timeframe_trend", "ema_trend").

    Returns:
        Dict mapping ``(symbol, timeframe_str)`` → warm limit (int).
    """
    # Map (symbol, tf) → max ema_trend seen across bots sharing that pair
    group_max_ema: dict[tuple[str, str], int] = {}

    for cfg in configs:
        symbol: str = cfg.get("symbol", "")
        tf_signal: str = cfg.get("timeframe_signal", "")
        tf_trend: str = cfg.get("timeframe_trend", "")
        ema_trend: int = int(cfg.get("ema_trend", 50))

        # Update both timeframes for this bot (deduped by dict key)
        for tf in (tf_signal, tf_trend):
            if not symbol or not tf:
                continue
            key = (symbol, tf)
            if key not in group_max_ema or ema_trend > group_max_ema[key]:
                group_max_ema[key] = ema_trend

    # Convert max ema_trend → warm limit
    return {
        key: max(100, ema_trend * 3)
        for key, ema_trend in group_max_ema.items()
    }


# ---------------------------------------------------------------------------
# Per-bot coroutine
# ---------------------------------------------------------------------------

async def run_bot(
    config_path: str,
    shared_exchange,
    shutdown_event: asyncio.Event,
    portfolio_manager: Optional[PortfolioManager] = None,
    risk_override: Optional[float] = None,
    leverage_override: Optional[int] = None,
    market_data: Optional[SharedMarketData] = None,
    wake_events: Optional[dict] = None,
) -> None:
    """Run a single bot coroutine inside the shared event loop.

    Args:
        config_path: Path to the bot's JSON config file.
        shared_exchange: Shared ccxt exchange instance (markets already loaded).
        shutdown_event: Global shutdown event — when set, all bots stop.
        portfolio_manager: Optional shared position tracker for global limits.
        risk_override: If set, overrides config's ``risk_per_trade`` for all
            bots (e.g. 0.01 for 1%).  Takes precedence over the config value.
        leverage_override: If set, overrides config's ``leverage`` for all
            bots.  Takes precedence over the config value.
        market_data: Optional SharedMarketData instance.  When set and
            CCBT_SHARED_MARKETDATA=='1', the engine's BybitClient will
            early-return balance/OHLCV from the shared cache rather than
            hitting the exchange on every tick.
        wake_events: Optional shared dict mapping normalised coin symbols to
            lists of per-engine asyncio.Events.  When provided (PR1+), the
            engine registers its own private Event and wakes early on WS
            triggers.  None (default) == flag-OFF / today's behaviour.
    """
    config = load_config(config_path)
    if config is None:
        logger.warning("bot_skipped_bad_config", extra={"config": config_path})
        return

    # Apply global overrides (--risk / --leverage CLI args)
    if risk_override is not None:
        config["risk_per_trade"] = risk_override
    if leverage_override is not None:
        config["leverage"] = leverage_override

    symbol = config.get("symbol", "unknown")
    logger.info("bot_starting", extra={"config": config_path, "symbol": symbol})

    try:
        engine = TradingEngine(
            config=config,
            shutdown_event=shutdown_event,
            shared_exchange=shared_exchange,
            portfolio_manager=portfolio_manager,
            market_data=market_data,
            recently_closed=portfolio_manager.recently_closed if portfolio_manager is not None else None,
            wake_events=wake_events,
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


async def async_main(
    config_files: list[str],
    max_global_positions: int = 10,
    risk_override: Optional[float] = None,
    leverage_override: Optional[int] = None,
) -> None:
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

    # Global portfolio manager — enforces cross-bot position limits
    portfolio_manager = PortfolioManager(max_positions=max_global_positions)
    logger.info(
        "portfolio_manager_created",
        extra={
            "max_global_positions": max_global_positions,
            "risk_override": risk_override,
            "leverage_override": leverage_override,
        },
    )

    # -----------------------------------------------------------------------
    # T4: Startup warm + stagger (flag-guarded).
    # When CCBT_SHARED_MARKETDATA == '1': build one SharedMarketData, warm
    # balance once and warm OHLCV for every unique (symbol, tf) group at the
    # correct limit, then stagger task launches ~200 ms apart to spread the
    # initial load across the exchange rate limiter.
    #
    # When flag is off: no warm, no stagger — byte-for-byte today's behaviour.
    # -----------------------------------------------------------------------
    shared_market_data: Optional[SharedMarketData] = None
    _flag = os.environ.get("CCBT_SHARED_MARKETDATA")

    if _flag == "1":
        # Load all configs up-front so we can compute the warm plan
        valid_configs: list[dict] = []
        for path in config_files:
            cfg = load_config(path)
            if cfg is not None:
                valid_configs.append(cfg)

        shared_market_data = SharedMarketData(exchange=shared_exchange)

        # --- Warm balance once ---
        logger.info("startup_warm_balance_start")
        try:
            shared_market_data.get_balance(fresh=True)
            logger.info("startup_warm_balance_done")
        except Exception as exc:  # noqa: BLE001
            logger.warning("startup_warm_balance_failed", extra={"error": str(exc)})

        # --- Warm OHLCV per (symbol, tf) group ---
        warm_plan = compute_warm_plan(valid_configs)
        logger.info(
            "startup_warm_ohlcv_start",
            extra={"pairs": len(warm_plan)},
        )
        for (sym, tf), limit in warm_plan.items():
            # Normalise symbol: BTCUSDT → BTC/USDT:USDT (same as BybitClient does)
            base = sym.replace("USDT", "").replace("/", "").replace(":USDT", "")
            ccxt_symbol = f"{base}/USDT:USDT"
            try:
                shared_market_data.get_ohlcv(ccxt_symbol, tf, limit=limit, fresh=True)
                logger.info(
                    "startup_warm_ohlcv_done",
                    extra={"symbol": ccxt_symbol, "tf": tf, "limit": limit},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "startup_warm_ohlcv_failed",
                    extra={"symbol": ccxt_symbol, "tf": tf, "error": str(exc)},
                )

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

    # PR1 — WS wake plumbing: shared registry mapping normalised coin symbols
    # (e.g. 'BTCUSDT') to lists of per-engine asyncio.Events.  Each engine
    # registers its own private Event here at __init__ time.  PR2 will attach
    # the user-data WS task that fires these events on SL/TP fills.  In PR1
    # the dict is populated but never fired externally — flag-OFF behaviour.
    wake_events: dict = {}

    # Launch all bots as concurrent tasks.
    # Flag ON:  stagger launches ~200 ms apart to spread initial load.
    # Flag OFF: all tasks created immediately (today's behaviour).
    _STAGGER_S = 0.2  # seconds between task launches when flag is on
    tasks = []
    for cfg in config_files:
        task = asyncio.create_task(
            run_bot(
                cfg,
                shared_exchange,
                shutdown_event,
                portfolio_manager=portfolio_manager,
                risk_override=risk_override,
                leverage_override=leverage_override,
                market_data=shared_market_data,
                wake_events=wake_events,
            ),
            name=f"bot-{Path(cfg).stem}",
        )
        tasks.append(task)
        if _flag == "1":
            await asyncio.sleep(_STAGGER_S)

    # Launch Telegram command handler alongside bots
    from bot.telegram_commands import run_telegram_handler
    telegram_task = asyncio.create_task(
        run_telegram_handler(shutdown_event, exchange=shared_exchange),
        name="telegram-handler",
    )
    tasks.append(telegram_task)

    bot_count = len(tasks) - 1  # Exclude telegram handler
    logger.info("all_bots_launched", extra={"count": bot_count})

    # Single summary alert instead of 167 individual start messages
    from bot.telegram import send_alert
    risk_info = f"{risk_override * 100:.1f}%" if risk_override is not None else "per-config"
    lev_info = f"{leverage_override}x" if leverage_override is not None else "per-config"
    send_alert(
        f"🟢 <b>CCBT Started</b>\n"
        f"Bots: {bot_count} | Max positions: {max_global_positions}\n"
        f"Exchange: {exchange_name}\n"
        f"Mode: {'testnet' if use_testnet else 'LIVE'}\n"
        f"Risk: {risk_info} | Leverage: {lev_info}",
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
    parser.add_argument(
        "--max-positions",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Maximum total open positions across ALL bots (default: 10). "
            "Prevents any single market move from hitting the full portfolio."
        ),
    )
    parser.add_argument(
        "--risk",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "Override risk_per_trade for ALL bots (e.g. 0.01 for 1%%). "
            "When omitted, each bot uses its own config value."
        ),
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=None,
        metavar="X",
        help=(
            "Override leverage for ALL bots (e.g. 25). "
            "When omitted, each bot uses its own config value."
        ),
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

    asyncio.run(
        async_main(
            config_files,
            max_global_positions=args.max_positions,
            risk_override=args.risk,
            leverage_override=args.leverage,
        )
    )


if __name__ == "__main__":
    main()
