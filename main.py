"""Entry point for the crypto trading bot."""

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


def load_config(path: str = "config.json") -> dict:
    """Load and validate configuration from JSON file.

    Args:
        path: Path to config file.

    Returns:
        Configuration dictionary.

    Raises:
        ValueError: If required keys are missing or values are out of range.
    """
    with open(path) as f:
        config = json.load(f)

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")

    # YOLO mode uses relaxed validation limits
    yolo_mode = os.getenv("YOLO_MODE", "").lower() in ("1", "true", "yes")
    max_leverage = 125 if yolo_mode else 25
    max_risk = 0.25 if yolo_mode else 0.1

    # Validate numeric ranges to prevent dangerous misconfigurations
    if not 1 <= config["leverage"] <= max_leverage:
        raise ValueError(f"leverage must be 1-{max_leverage}, got {config['leverage']}")
    if not 0.001 <= config["risk_per_trade"] <= max_risk:
        raise ValueError(f"risk_per_trade must be 0.1%-{max_risk*100:.0f}%, got {config['risk_per_trade']}")
    if not 0.005 <= config["max_daily_loss"] <= 0.5:
        raise ValueError(f"max_daily_loss must be 0.5%-50%, got {config['max_daily_loss']}")
    if config["atr_sl_mult"] <= 0:
        raise ValueError("atr_sl_mult must be > 0")
    # atr_tp_mult == 0 is valid for trailing-only strategies (no fixed TP)
    if config["atr_tp_mult"] < 0:
        raise ValueError("atr_tp_mult must be >= 0")
    if config.get("atr_trail_mult", config["atr_sl_mult"]) <= 0:
        raise ValueError("atr_trail_mult must be > 0")

    # YOLO mode safety: must be testnet
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


def handle_shutdown(signum, frame) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("shutdown_signal_received", extra={"signal": signum})
    shutdown_event.set()


async def trading_loop(config: dict) -> None:
    """Construct TradingEngine and run the main loop.

    Args:
        config: Bot configuration.
    """
    global shutdown_event
    shutdown_event = asyncio.Event()  # Create inside the running loop

    # Launch Telegram command handler as a background task (single-bot mode)
    from bot.telegram_commands import run_telegram_handler
    telegram_task = asyncio.create_task(
        run_telegram_handler(shutdown_event),
        name="telegram-handler",
    )

    engine = TradingEngine(config=config, shutdown_event=shutdown_event)
    try:
        await engine.run()
    finally:
        # Ensure handler task is cancelled when the engine exits
        telegram_task.cancel()
        try:
            await telegram_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
    parser.add_argument(
        "--config", default=os.getenv("CONFIG_FILE", "config.json"),
        help="Path to config file (default: config.json, or CONFIG_FILE env var)",
    )
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)

    logger.info(
        "config_loaded",
        extra={
            "config_file": args.config,
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
