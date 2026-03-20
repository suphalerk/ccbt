"""Entry point for the OANDA gold (XAU/USD) trading bot.

Loads config_gold_forex.json by default and wires OandaClient into
TradingEngine instead of BybitClient. All other logic (signal generation,
risk management, AI advisor) is identical to main.py.

Usage:
    python main_gold.py
    python main_gold.py --config config_gold_forex.json
    python main_gold.py --config my_custom_gold.json
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

from bot.engine import TradingEngine
from bot.logger import setup_logging

load_dotenv()

logger = logging.getLogger(__name__)

# Graceful shutdown event — created lazily inside the running event loop
shutdown_event: asyncio.Event = None  # type: ignore[assignment]

REQUIRED_CONFIG_KEYS = [
    "symbol", "timeframe_signal", "timeframe_trend", "leverage",
    "risk_per_trade", "max_daily_loss", "max_positions",
    "ema_fast", "ema_slow", "ema_trend", "rsi_period",
    "rsi_min", "rsi_max", "atr_period", "atr_sl_mult", "atr_tp_mult",
]


def load_config(path: str = "config_gold_forex.json") -> dict:
    """Load and validate configuration from JSON file.

    Applies the same validation rules as main.py's load_config(), with the
    addition of checking that the exchange is set to "oanda" and required
    OANDA env vars are present.

    Args:
        path: Path to config file.

    Returns:
        Configuration dictionary.

    Raises:
        ValueError: If required keys are missing, values are out of range, or
                    OANDA credentials are not set in the environment.
    """
    with open(path) as f:
        config = json.load(f)

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")

    # Validate that this config targets OANDA
    exchange = config.get("exchange", "").lower()
    if exchange != "oanda":
        raise ValueError(
            f"main_gold.py expects exchange='oanda' in config, got '{exchange}'. "
            "Use main.py for Bybit/Binance."
        )

    # Check OANDA credentials before anything else
    if not os.getenv("OANDA_API_TOKEN"):
        raise ValueError(
            "OANDA_API_TOKEN is not set. Add it to your .env file."
        )
    if not os.getenv("OANDA_ACCOUNT_ID"):
        raise ValueError(
            "OANDA_ACCOUNT_ID is not set. Add it to your .env file."
        )

    # YOLO mode uses relaxed validation limits
    yolo_mode = os.getenv("YOLO_MODE", "").lower() in ("1", "true", "yes")
    max_leverage = 125 if yolo_mode else 50  # OANDA allows up to ~500:1 (forex)
    max_risk = 0.25 if yolo_mode else 0.1

    if not 1 <= config["leverage"] <= max_leverage:
        raise ValueError(
            f"leverage must be 1-{max_leverage}, got {config['leverage']}"
        )
    if not 0.001 <= config["risk_per_trade"] <= max_risk:
        raise ValueError(
            f"risk_per_trade must be 0.1%-{max_risk*100:.0f}%, "
            f"got {config['risk_per_trade']}"
        )
    if not 0.005 <= config["max_daily_loss"] <= 0.5:
        raise ValueError(
            f"max_daily_loss must be 0.5%-50%, got {config['max_daily_loss']}"
        )
    if config["atr_sl_mult"] <= 0 or config["atr_tp_mult"] <= 0:
        raise ValueError("atr_sl_mult and atr_tp_mult must be > 0")
    if config.get("atr_trail_mult", config["atr_sl_mult"]) <= 0:
        raise ValueError("atr_trail_mult must be > 0")

    if yolo_mode and not config.get("use_testnet", True):
        raise ValueError("YOLO mode requires use_testnet=true for safety")

    # AI layer validation
    ai_cfg = config.get("ai_layer", {})
    if ai_cfg.get("enabled"):
        if ai_cfg.get("max_tokens", 1024) < 256:
            raise ValueError("ai max_tokens must be >= 256")
        if not 0.0 <= ai_cfg.get("confidence_threshold", 0.65) <= 1.0:
            raise ValueError("confidence_threshold must be 0-1")

    if yolo_mode:
        logger.warning(
            "yolo_mode_active",
            extra={
                "leverage": config["leverage"],
                "risk_per_trade": config["risk_per_trade"],
                "config_file": path,
            },
        )

    return config


def _patch_engine_with_oanda(config: dict) -> None:
    """Monkey-patch TradingEngine to use OandaClient instead of BybitClient.

    This approach keeps the TradingEngine import-clean — it only imports
    BybitClient at the module level. By patching before instantiation we
    avoid modifying engine.py.

    The patch is applied at the ``bot.engine`` module level so that
    ``TradingEngine.__init__`` picks up OandaClient when it does:
        ``self.client = BybitClient(config)``

    This is safe for the gold bot because:
    1. main_gold.py is the only process using this patched module.
    2. OandaClient has an identical public interface (duck typing).
    3. The patch is process-scoped, not affecting other modules.
    """
    import bot.engine as engine_mod
    from bot.forex_exchange import OandaClient

    # Replace BybitClient reference in the engine module namespace
    engine_mod.BybitClient = OandaClient  # type: ignore[attr-defined]
    logger.info(
        "engine_patched",
        extra={"client": "OandaClient", "symbol": config.get("symbol")},
    )


def handle_shutdown(signum, frame) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("shutdown_signal_received", extra={"signal": signum})
    shutdown_event.set()


async def trading_loop(config: dict) -> None:
    """Construct TradingEngine with OandaClient and run the main loop.

    Args:
        config: Bot configuration.
    """
    global shutdown_event
    shutdown_event = asyncio.Event()
    engine = TradingEngine(config=config, shutdown_event=shutdown_event)
    await engine.run()


def main() -> None:
    """Main entry point for the gold trading bot."""
    parser = argparse.ArgumentParser(description="Gold (XAU/USD) Trading Bot — OANDA")
    parser.add_argument(
        "--config",
        default=os.getenv("CONFIG_FILE", "config_gold_forex.json"),
        help="Path to config file (default: config_gold_forex.json, or CONFIG_FILE env var)",
    )
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)

    # Patch engine BEFORE constructing TradingEngine
    _patch_engine_with_oanda(config)

    logger.info(
        "config_loaded",
        extra={
            "config_file": args.config,
            "exchange": "oanda",
            "symbol": config.get("symbol"),
            "testnet": config.get("use_testnet", True),
            "ai_layer": config.get("ai_layer", {}).get("enabled", False),
        },
    )

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Run trading loop
    asyncio.run(trading_loop(config))


if __name__ == "__main__":
    main()
