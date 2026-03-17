"""Entry point for the crypto trading bot."""

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bot.exchange import BybitClient
from bot.logger import TradeJournal, setup_logging
from bot.risk import RiskManager
from bot.strategy import SignalType, generate_signal

logger = logging.getLogger(__name__)

# Graceful shutdown
shutdown_event = asyncio.Event()


def load_config(path: str = "config.json") -> dict:
    """Load configuration from JSON file.

    Args:
        path: Path to config file.

    Returns:
        Configuration dictionary.
    """
    with open(path) as f:
        return json.load(f)


async def trading_loop(config: dict) -> None:
    """Main trading loop.

    Args:
        config: Bot configuration.
    """
    # Initialize components
    client = BybitClient(config)
    balance = client.get_balance()
    risk_mgr = RiskManager(config, balance)
    journal = TradeJournal()

    # Set leverage
    client.set_leverage(config["leverage"])

    logger.info(
        "bot_started",
        extra={
            "symbol": config["symbol"],
            "balance": balance,
            "leverage": config["leverage"],
            "testnet": config.get("use_testnet", True),
        },
    )

    last_daily_reset = datetime.now(tz=timezone.utc).date()

    while not shutdown_event.is_set():
        try:
            # Daily reset check
            today = datetime.now(tz=timezone.utc).date()
            if today != last_daily_reset:
                balance = client.get_balance()
                risk_mgr.reset_daily(balance)
                last_daily_reset = today
                logger.info("daily_reset_triggered", extra={"date": str(today)})

            # Get current positions
            positions = client.get_positions()
            num_positions = len(positions)

            # Check if we can trade
            balance = client.get_balance()
            can_trade, reason = risk_mgr.can_trade(balance, num_positions)

            if not can_trade:
                logger.info("trade_skipped", extra={"reason": reason})
                await asyncio.sleep(60)
                continue

            # Fetch data
            signal_df = client.get_ohlcv(
                config["symbol"], config["timeframe_signal"], limit=100
            )
            trend_df = client.get_ohlcv(
                config["symbol"], config["timeframe_trend"], limit=100
            )

            risk_mgr.clear_api_errors()

            # Generate signal
            trade_signal = generate_signal(signal_df, trend_df, config)

            if trade_signal is not None:
                # Validate order through risk manager
                approved, reason, position_size = risk_mgr.validate_order(
                    balance=balance,
                    entry_price=trade_signal.entry_price,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    num_open_positions=num_positions,
                )

                if approved:
                    # Convert USDT size to contract size
                    contract_size = position_size / trade_signal.entry_price

                    # Place order
                    side = "buy" if trade_signal.signal_type == SignalType.LONG else "sell"
                    order = client.place_order(
                        side=side,
                        size=round(contract_size, 6),
                        sl=round(trade_signal.stop_loss, 2),
                        tp=round(trade_signal.take_profit, 2),
                    )

                    # Log trade
                    journal.log_trade_open(
                        symbol=config["symbol"],
                        side=side,
                        entry_price=trade_signal.entry_price,
                        size=contract_size,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                    )

                    logger.info(
                        "trade_executed",
                        extra={
                            "order_id": order.order_id,
                            "side": side,
                            "size": contract_size,
                            "entry": trade_signal.entry_price,
                            "sl": trade_signal.stop_loss,
                            "tp": trade_signal.take_profit,
                            "risk_usd": balance * config["risk_per_trade"],
                        },
                    )
                else:
                    logger.info("trade_rejected", extra={"reason": reason})

            # Wait for next candle interval
            # Sleep for 60 seconds between checks
            await asyncio.sleep(60)

        except KeyboardInterrupt:
            break
        except Exception as e:
            risk_mgr.record_api_error()
            logger.error("trading_loop_error", extra={"error": str(e)})

            # Check if we should halt
            can_trade, reason = risk_mgr.can_trade(balance, 0)
            if not can_trade and "API error" in reason:
                logger.critical("bot_halted_api_errors", extra={"reason": reason})
                # Emergency: cancel all orders and close positions
                try:
                    client.cancel_all_orders()
                    client.close_all_positions()
                except Exception:
                    pass
                break

            await asyncio.sleep(30)

    logger.info("bot_stopped")


def handle_shutdown(signum, frame) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("shutdown_signal_received", extra={"signal": signum})
    shutdown_event.set()


def main() -> None:
    """Main entry point."""
    setup_logging()
    config = load_config()

    logger.info("config_loaded", extra={"testnet": config.get("use_testnet", True)})

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Run trading loop
    asyncio.run(trading_loop(config))


if __name__ == "__main__":
    main()
