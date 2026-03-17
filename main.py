"""Entry point for the crypto trading bot."""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from bot.ai_analyst import AIAnalyst, AIDecision, CandidateSignal
from bot.context_builder import ContextBuilder
from bot.exchange import BybitClient
from bot.logger import TradeJournal, setup_logging
from bot.news_fetcher import NewsFetcher
from bot.risk import RiskManager
from bot.strategy import SignalType, generate_signal

load_dotenv()

logger = logging.getLogger(__name__)

# Graceful shutdown
shutdown_event = asyncio.Event()

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
        ValueError: If required keys are missing.
    """
    with open(path) as f:
        config = json.load(f)

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")

    return config


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

    # Initialize AI layer
    ai_config = config.get("ai_layer", {})
    ai_enabled = ai_config.get("enabled", False)

    news_fetcher = NewsFetcher(
        source=ai_config.get("news_source", "rss"),
        cryptopanic_token=os.getenv("CRYPTOPANIC_TOKEN"),
    )

    ctx_builder = ContextBuilder(client, config, news_fetcher)

    ai_analyst = AIAnalyst(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        confidence_threshold=ai_config.get("confidence_threshold", 0.65),
        model=ai_config.get("model", "claude-sonnet-4-6"),
        max_tokens=ai_config.get("max_tokens", 512),
        timeout_seconds=ai_config.get("timeout_seconds", 8.0),
        fallback_on_timeout=ai_config.get("fallback_on_timeout", "skip"),
        enabled=ai_enabled,
    )

    confidence_threshold = ai_config.get("confidence_threshold", 0.65)
    log_all_decisions = ai_config.get("log_all_decisions", True)

    # Set leverage
    client.set_leverage(config["leverage"])

    log_level = "warning" if not config.get("use_testnet", True) else "info"
    logger.log(
        logging.WARNING if log_level == "warning" else logging.INFO,
        "bot_started",
        extra={
            "symbol": config["symbol"],
            "balance": balance,
            "leverage": config["leverage"],
            "mode": "LIVE" if not config.get("use_testnet", True) else "testnet",
            "ai_layer_enabled": ai_enabled,
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

            # Fetch data with error handling
            try:
                signal_df = client.get_ohlcv(
                    config["symbol"], config["timeframe_signal"], limit=100
                )
                trend_df = client.get_ohlcv(
                    config["symbol"], config["timeframe_trend"], limit=100
                )
                risk_mgr.clear_api_errors()
            except Exception as e:
                risk_mgr.record_api_error()
                logger.error("data_fetch_error", extra={"error": str(e)})
                await asyncio.sleep(30)
                continue

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
                    # Validate leverage before placing order
                    if not risk_mgr.check_leverage(position_size, balance):
                        logger.warning(
                            "trade_rejected_leverage",
                            extra={
                                "position_size": position_size,
                                "balance": balance,
                                "actual_leverage": position_size / balance if balance > 0 else 0,
                                "max_leverage": config["leverage"],
                            },
                        )
                        await asyncio.sleep(60)
                        continue

                    side = "buy" if trade_signal.signal_type == SignalType.LONG else "sell"

                    # AI gate
                    ai_result = None
                    if ai_enabled:
                        # Build candidate signal for AI
                        sl_pct = abs(trade_signal.entry_price - trade_signal.stop_loss) / trade_signal.entry_price * 100
                        tp_pct = abs(trade_signal.take_profit - trade_signal.entry_price) / trade_signal.entry_price * 100
                        candidate = CandidateSignal(
                            side="long" if trade_signal.signal_type == SignalType.LONG else "short",
                            entry_price=trade_signal.entry_price,
                            stop_loss=trade_signal.stop_loss,
                            take_profit=trade_signal.take_profit,
                            sl_pct=sl_pct,
                            tp_pct=tp_pct,
                            rr_ratio=trade_signal.risk_reward_ratio,
                        )

                        # Build market context
                        market_ctx = await ctx_builder.build(
                            config["symbol"], risk_mgr.state
                        )

                        # Get AI decision
                        ai_result = await ai_analyst.analyze(candidate, market_ctx)

                        logger.info(
                            "ai_decision",
                            extra={
                                "decision": ai_result.decision.value,
                                "confidence": ai_result.confidence,
                                "reasoning": ai_result.reasoning,
                                "flags": ai_result.risk_flags,
                            },
                        )

                        # Gate: check decision and confidence
                        if ai_result.decision in (AIDecision.SKIP, AIDecision.WAIT):
                            if log_all_decisions:
                                journal.log_ai_decision(
                                    symbol=config["symbol"],
                                    side=side,
                                    entry_price=trade_signal.entry_price,
                                    ai_decision=ai_result.decision.value,
                                    ai_confidence=ai_result.confidence,
                                    ai_reasoning=ai_result.reasoning,
                                    ai_risk_flags=ai_result.risk_flags,
                                    ai_override=ai_result.override,
                                )
                            logger.info(
                                f"trade_{ai_result.decision.value}_by_ai",
                                extra={"reasoning": ai_result.reasoning},
                            )
                            await asyncio.sleep(60)
                            continue

                        # EXECUTE but check confidence threshold
                        if ai_result.confidence < confidence_threshold:
                            if log_all_decisions:
                                journal.log_ai_decision(
                                    symbol=config["symbol"],
                                    side=side,
                                    entry_price=trade_signal.entry_price,
                                    ai_decision="low_confidence",
                                    ai_confidence=ai_result.confidence,
                                    ai_reasoning=ai_result.reasoning,
                                    ai_risk_flags=ai_result.risk_flags,
                                    ai_override=ai_result.override,
                                )
                            logger.info(
                                "trade_skipped_low_confidence",
                                extra={
                                    "confidence": ai_result.confidence,
                                    "threshold": confidence_threshold,
                                },
                            )
                            await asyncio.sleep(60)
                            continue

                    # Execute trade
                    contract_size = position_size / trade_signal.entry_price

                    order = client.place_order(
                        side=side,
                        size=round(contract_size, 6),
                        sl=round(trade_signal.stop_loss, 2),
                        tp=round(trade_signal.take_profit, 2),
                    )

                    # Log trade with AI decision data
                    journal.log_trade_open(
                        symbol=config["symbol"],
                        side=side,
                        entry_price=trade_signal.entry_price,
                        size=contract_size,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        ai_decision=ai_result.decision.value if ai_result else None,
                        ai_confidence=ai_result.confidence if ai_result else None,
                        ai_reasoning=ai_result.reasoning if ai_result else None,
                        ai_risk_flags=ai_result.risk_flags if ai_result else None,
                        ai_override=ai_result.override if ai_result else False,
                    )

                    ctx_builder.record_trade_time()

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
                            "ai_decision": ai_result.decision.value if ai_result else "disabled",
                            "ai_confidence": ai_result.confidence if ai_result else None,
                        },
                    )
                else:
                    logger.info("trade_rejected", extra={"reason": reason})

            # Wait for next candle interval
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

    logger.info(
        "config_loaded",
        extra={
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
