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
from typing import Optional

from dotenv import load_dotenv

from bot.ai_analyst import AIAnalyst, CandidateSignal
from bot.context_builder import ContextBuilder
from bot.exchange import BybitClient
from bot.logger import CalibrationTracker, TradeJournal, setup_logging
from bot.news_fetcher import NewsFetcher
from bot.risk import RiskManager
from bot.strategy import SignalType, compute_net_rr, compute_trailing_stop, generate_signal

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


def apply_ai_adjustments(
    stop_loss: float,
    take_profit: float,
    entry_price: float,
    position_size: float,
    ai_result,
    signal_type: SignalType,
    influence_multiplier: float = 1.0,
) -> tuple[float, float, float]:
    """Apply AI advisor adjustments to trade parameters.

    Scales position size and adjusts SL/TP based on the AI advisor's
    recommendations, modulated by the influence multiplier (which is
    based on the AI's rolling accuracy).

    Args:
        stop_loss: Original stop loss price.
        take_profit: Original take profit price.
        entry_price: Entry price.
        position_size: Original position size in USDT.
        ai_result: AnalystResult from AI advisor.
        signal_type: LONG or SHORT.
        influence_multiplier: Calibration-based influence (0.5-1.25).

    Returns:
        Tuple of (adjusted_position_size, adjusted_sl, adjusted_tp).
    """
    # Scale position size modifier toward 1.0 based on influence
    # If influence is 0.5 and AI says 1.3, effective modifier = 1 + 0.5*(1.3-1) = 1.15
    raw_size_mod = ai_result.position_size_modifier
    effective_size_mod = 1.0 + influence_multiplier * (raw_size_mod - 1.0)
    effective_size_mod = max(0.5, min(1.5, effective_size_mod))
    adjusted_size = position_size * effective_size_mod

    # Adjust SL distance
    sl_distance = abs(entry_price - stop_loss)
    raw_sl_adj = ai_result.sl_adjustment
    effective_sl_adj = 1.0 + influence_multiplier * (raw_sl_adj - 1.0)
    effective_sl_adj = max(0.8, min(1.3, effective_sl_adj))
    adjusted_sl_distance = sl_distance * effective_sl_adj

    if signal_type == SignalType.LONG:
        adjusted_sl = entry_price - adjusted_sl_distance
    else:
        adjusted_sl = entry_price + adjusted_sl_distance

    # Adjust TP distance
    tp_distance = abs(take_profit - entry_price)
    raw_tp_adj = ai_result.tp_adjustment
    effective_tp_adj = 1.0 + influence_multiplier * (raw_tp_adj - 1.0)
    effective_tp_adj = max(0.7, min(1.5, effective_tp_adj))
    adjusted_tp_distance = tp_distance * effective_tp_adj

    if signal_type == SignalType.LONG:
        adjusted_tp = entry_price + adjusted_tp_distance
    else:
        adjusted_tp = entry_price - adjusted_tp_distance

    return adjusted_size, adjusted_sl, adjusted_tp


def check_closed_positions(
    open_trade_ids: dict,
    current_positions: list,
    journal: TradeJournal,
    risk_mgr: RiskManager,
    calibration_tracker: Optional[CalibrationTracker],
    symbol: str,
    client=None,
) -> dict:
    """Detect positions closed by exchange (SL/TP fill) and update state.

    Compares tracked open trades against current exchange positions.
    When a tracked trade is no longer open on the exchange, it has been
    closed by SL or TP. Queries exchange for actual fill data to determine
    whether TP or SL was hit.

    Args:
        open_trade_ids: Dict of {trade_id: {side, entry_price, size, sl, tp, calibration_id, signal_type, atr, open_time}}.
        current_positions: Current positions from exchange.
        journal: Trade journal for logging closes.
        risk_mgr: Risk manager for recording PnL.
        calibration_tracker: Optional calibration tracker.
        symbol: Trading symbol.
        client: BybitClient instance for fetching actual trade data.

    Returns:
        Updated open_trade_ids dict with closed trades removed.
    """
    if not open_trade_ids:
        return open_trade_ids

    # Determine which sides have active positions on exchange
    active_sides = set()
    def _normalize_symbol(s: str) -> str:
        return s.replace("/", "").replace(":USDT", "").replace("-", "").upper()
    norm_symbol = _normalize_symbol(symbol)
    for pos in current_positions:
        if _normalize_symbol(pos.get("symbol", "")) == norm_symbol:
            side = pos.get("side", "")
            active_sides.add(side)

    # Check each tracked trade
    closed = []
    for trade_id, info in open_trade_ids.items():
        trade_side = "long" if info["side"] == "buy" else "short"
        if trade_side not in active_sides:
            # Position was closed by exchange (SL or TP hit)
            entry = info["entry_price"]
            sl = info["sl"]
            tp = info["tp"]
            duration = int(time.time() - info["open_time"])

            # Compute both possible PnLs
            if trade_side == "long":
                sl_pnl = (sl - entry) / entry * info["size"]
                tp_pnl = (tp - entry) / entry * info["size"]
            else:
                sl_pnl = (entry - sl) / entry * info["size"]
                tp_pnl = (entry - tp) / entry * info["size"]

            # Try to get actual fill price from exchange trade history
            actual_pnl = None
            exit_price = None
            close_reason = "unknown"

            if client is not None:
                try:
                    since_ms = int(info["open_time"] * 1000)
                    recent_trades = client.get_closed_pnl(symbol, since_ms=since_ms)
                    # Find the closing trade: opposite side to our entry
                    close_side = "sell" if info["side"] == "buy" else "buy"
                    for t in reversed(recent_trades):
                        if t["side"] == close_side and t["amount"] > 0:
                            exit_price = t["price"]
                            # Calculate actual PnL from fill price
                            if trade_side == "long":
                                actual_pnl = (exit_price - entry) / entry * info["size"]
                            else:
                                actual_pnl = (entry - exit_price) / entry * info["size"]
                            break
                except Exception as e:
                    logger.warning("failed_to_fetch_actual_pnl", extra={"error": str(e)})

            if actual_pnl is not None and exit_price is not None:
                # Determine close reason from exit price proximity to SL/TP
                dist_to_sl = abs(exit_price - sl)
                dist_to_tp = abs(exit_price - tp)
                close_reason = "tp" if dist_to_tp < dist_to_sl else "sl"
                estimated_pnl = actual_pnl
            else:
                # Fallback: infer from current price if available
                if client is not None:
                    try:
                        current_price = client.get_ticker_price(symbol)
                        # If price is beyond TP, it was likely TP hit
                        if trade_side == "long":
                            if current_price >= tp:
                                estimated_pnl = tp_pnl
                                exit_price = tp
                                close_reason = "tp"
                            else:
                                estimated_pnl = sl_pnl
                                exit_price = sl
                                close_reason = "sl"
                        else:
                            if current_price <= tp:
                                estimated_pnl = tp_pnl
                                exit_price = tp
                                close_reason = "tp"
                            else:
                                estimated_pnl = sl_pnl
                                exit_price = sl
                                close_reason = "sl"
                    except Exception:
                        estimated_pnl = sl_pnl
                        exit_price = sl
                        close_reason = "sl"
                else:
                    estimated_pnl = sl_pnl
                    exit_price = sl
                    close_reason = "sl"

            pnl_pct = estimated_pnl / info["size"] * 100 if info["size"] > 0 else 0

            journal.log_trade_close(
                trade_id=trade_id,
                exit_price=exit_price,
                pnl=estimated_pnl,
                pnl_pct=pnl_pct,
                close_reason=close_reason,
                duration_seconds=duration,
            )

            risk_mgr.record_trade_result(estimated_pnl)

            if calibration_tracker and info.get("calibration_id"):
                outcome = "win" if estimated_pnl > 0 else "loss"
                calibration_tracker.record_outcome(
                    info["calibration_id"], outcome, estimated_pnl
                )

            logger.info(
                "position_closed_detected",
                extra={
                    "trade_id": trade_id,
                    "side": info["side"],
                    "entry": entry,
                    "exit": exit_price,
                    "pnl": round(estimated_pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "close_reason": close_reason,
                    "duration_s": duration,
                },
            )
            closed.append(trade_id)

    for tid in closed:
        del open_trade_ids[tid]

    return open_trade_ids


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

    # Initialize calibration tracker
    calibration_tracker = CalibrationTracker() if ai_enabled else None

    ctx_builder = ContextBuilder(
        client, config, news_fetcher, trade_journal=journal
    )

    ai_analyst = AIAnalyst(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        confidence_threshold=ai_config.get("confidence_threshold", 0.65),
        model=ai_config.get("model", "claude-sonnet-4-6"),
        max_tokens=ai_config.get("max_tokens", 1024),
        timeout_seconds=ai_config.get("timeout_seconds", 10.0),
        fallback_on_timeout=ai_config.get("fallback_on_timeout", "execute"),
        enabled=ai_enabled,
        calibration_tracker=calibration_tracker,
    )

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
            "ai_mode": "advisor" if ai_enabled else "disabled",
        },
    )

    last_daily_reset = datetime.now(tz=timezone.utc).date()

    # Track open trades for position monitoring
    # {trade_id: {side, entry_price, size, sl, tp, calibration_id, signal_type, atr, open_time}}
    tracked_trades: dict[int, dict] = {}

    # Restore tracking for any positions already open on exchange (e.g., after restart)
    # Only restore ONE trade per side to avoid double-counting PnL
    # (Bybit merges same-direction positions into one net position)
    try:
        existing_positions = client.get_positions()
        open_db_trades = journal.get_open_trades()
        restored_sides: set[str] = set()
        for db_trade in open_db_trades:
            trade_id = db_trade["id"]
            trade_side = "long" if db_trade["side"] == "buy" else "short"
            if trade_side in restored_sides:
                continue  # Already restored one trade for this side
            # Only restore if position is still active on exchange
            for pos in existing_positions:
                pos_side = pos.get("side", "")
                if pos_side == trade_side:
                    tracked_trades[trade_id] = {
                        "side": db_trade["side"],
                        "entry_price": db_trade["entry_price"],
                        "size": db_trade["size"] * db_trade["entry_price"],
                        "sl": db_trade.get("stop_loss", 0),
                        "tp": db_trade.get("take_profit", 0),
                        "calibration_id": None,
                        "signal_type": SignalType.LONG if db_trade["side"] == "buy" else SignalType.SHORT,
                        "atr": 0,  # Unknown after restart; trailing stop won't move without ATR
                        "open_time": time.time(),
                    }
                    restored_sides.add(trade_side)
                    logger.info(
                        "restored_tracked_trade",
                        extra={"trade_id": trade_id, "side": db_trade["side"]},
                    )
                    break
    except Exception as e:
        logger.warning("failed_to_restore_tracked_trades", extra={"error": str(e)})

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

            # Check for positions closed by exchange (SL/TP fills)
            tracked_trades = check_closed_positions(
                open_trade_ids=tracked_trades,
                current_positions=positions,
                journal=journal,
                risk_mgr=risk_mgr,
                calibration_tracker=calibration_tracker,
                symbol=config["symbol"],
                client=client,
            )

            # Update trailing stops for open positions
            if tracked_trades:
                try:
                    current_price = client.get_ticker_price(config["symbol"])
                    for trade_id, info in tracked_trades.items():
                        # Skip trailing stop for restored trades without ATR data
                        if info["atr"] <= 0:
                            continue
                        sig_type = info["signal_type"]
                        new_sl = compute_trailing_stop(
                            current_price, info["sl"], info["atr"],
                            sig_type, config,
                        )
                        if new_sl != info["sl"]:
                            # Update SL on exchange, not just locally
                            old_sl = info["sl"]
                            success = client.modify_sl(
                                symbol=config["symbol"],
                                side=info["side"],
                                new_sl=round(new_sl, 2),
                            )
                            if success:
                                info["sl"] = new_sl
                                logger.info(
                                    "trailing_stop_updated",
                                    extra={
                                        "trade_id": trade_id,
                                        "old_sl": round(old_sl, 2),
                                        "new_sl": round(new_sl, 2),
                                        "current_price": current_price,
                                    },
                                )
                            else:
                                logger.warning(
                                    "trailing_stop_exchange_update_failed",
                                    extra={"trade_id": trade_id, "new_sl": new_sl},
                                )
                except Exception as e:
                    logger.warning("trailing_stop_error", extra={"error": str(e)})

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
                    regime=trade_signal.regime,
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

                    # AI advisor
                    ai_result = None
                    calibration_id = None
                    adjusted_sl = trade_signal.stop_loss
                    adjusted_tp = trade_signal.take_profit
                    adjusted_size = position_size

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

                        # Get AI advisor decision
                        ai_result = await ai_analyst.analyze(candidate, market_ctx)

                        # Record decision for calibration
                        if calibration_tracker:
                            calibration_id = calibration_tracker.record_decision(
                                symbol=config["symbol"],
                                side=side,
                                entry_price=trade_signal.entry_price,
                                stated_confidence=ai_result.confidence,
                                position_size_modifier=ai_result.position_size_modifier,
                                sl_adjustment=ai_result.sl_adjustment,
                                tp_adjustment=ai_result.tp_adjustment,
                                market_regime=ai_result.market_regime,
                                reasoning=ai_result.reasoning,
                                risk_flags=ai_result.risk_flags,
                                should_skip=(ai_result.decision == "skip"),
                            )

                        logger.info(
                            "ai_advisor_result",
                            extra={
                                "decision": ai_result.decision,
                                "confidence": ai_result.confidence,
                                "calibrated_confidence": ai_result.calibrated_confidence,
                                "position_size_modifier": ai_result.position_size_modifier,
                                "sl_adjustment": ai_result.sl_adjustment,
                                "tp_adjustment": ai_result.tp_adjustment,
                                "market_regime": ai_result.market_regime,
                                "reasoning": ai_result.reasoning,
                                "flags": ai_result.risk_flags,
                            },
                        )

                        # Only skip for hard-stop conditions (AI says should_skip)
                        if ai_result.decision == "skip":
                            if log_all_decisions:
                                journal.log_ai_decision(
                                    symbol=config["symbol"],
                                    side=side,
                                    entry_price=trade_signal.entry_price,
                                    ai_decision="skip",
                                    ai_confidence=ai_result.confidence,
                                    ai_reasoning=ai_result.reasoning,
                                    ai_risk_flags=ai_result.risk_flags,
                                    ai_override=False,
                                )
                            logger.info(
                                "trade_skipped_by_ai_hard_stop",
                                extra={"reasoning": ai_result.reasoning},
                            )
                            await asyncio.sleep(60)
                            continue

                        # Apply AI adjustments to trade parameters
                        influence_mult = (
                            calibration_tracker.get_influence_multiplier()
                            if calibration_tracker
                            else 1.0
                        )

                        adjusted_size, adjusted_sl, adjusted_tp = (
                            apply_ai_adjustments(
                                stop_loss=trade_signal.stop_loss,
                                take_profit=trade_signal.take_profit,
                                entry_price=trade_signal.entry_price,
                                position_size=position_size,
                                ai_result=ai_result,
                                signal_type=trade_signal.signal_type,
                                influence_multiplier=influence_mult,
                            )
                        )

                        # Re-validate net R:R after AI adjustments
                        post_ai_rr = compute_net_rr(
                            trade_signal.entry_price, adjusted_sl, adjusted_tp, config,
                        )
                        min_rr = config.get("min_rr_ratio", 1.8)
                        if post_ai_rr < min_rr:
                            logger.warning(
                                "ai_adjustments_broke_rr",
                                extra={
                                    "post_ai_rr": round(post_ai_rr, 2),
                                    "min_rr": min_rr,
                                    "reverting": True,
                                },
                            )
                            # Revert to original SL/TP, keep only size adjustment
                            adjusted_sl = trade_signal.stop_loss
                            adjusted_tp = trade_signal.take_profit

                        # Re-validate leverage after AI size adjustment
                        if not risk_mgr.check_leverage(adjusted_size, balance):
                            adjusted_size = balance * config["leverage"]
                            logger.warning(
                                "ai_size_capped_by_leverage",
                                extra={"capped_size": adjusted_size},
                            )

                    # Execute trade with (potentially adjusted) parameters
                    contract_size = adjusted_size / trade_signal.entry_price

                    order = client.place_order(
                        side=side,
                        size=round(contract_size, 6),
                        sl=round(adjusted_sl, 2),
                        tp=round(adjusted_tp, 2),
                    )

                    # Verify SL/TP was actually set on the position
                    # If not, set them separately via trading stop API
                    try:
                        verify_positions = client.get_positions()
                        pos_has_sl = False
                        for vp in verify_positions:
                            if vp.get("side") == ("long" if side == "buy" else "short"):
                                sl_val = float(vp.get("stopLossPrice") or vp.get("info", {}).get("stopLoss", 0) or 0)
                                if sl_val > 0:
                                    pos_has_sl = True
                                break
                        if not pos_has_sl:
                            logger.warning("sl_not_set_on_order_retrying")
                            client.modify_sl(
                                symbol=config["symbol"],
                                side=side,
                                new_sl=round(adjusted_sl, 2),
                            )
                    except Exception as e:
                        logger.error("sl_verification_failed", extra={"error": str(e)})

                    # Use actual fill price if available, else fall back to signal price
                    actual_entry = order.price if order.price else trade_signal.entry_price

                    # Log trade with AI advisor data
                    trade_id = journal.log_trade_open(
                        symbol=config["symbol"],
                        side=side,
                        entry_price=actual_entry,
                        size=contract_size,
                        stop_loss=adjusted_sl,
                        take_profit=adjusted_tp,
                        ai_decision=ai_result.decision if ai_result else None,
                        ai_confidence=ai_result.confidence if ai_result else None,
                        ai_reasoning=ai_result.reasoning if ai_result else None,
                        ai_risk_flags=ai_result.risk_flags if ai_result else None,
                        ai_override=False,
                    )

                    # Track for position monitoring
                    tracked_trades[trade_id] = {
                        "side": side,
                        "entry_price": actual_entry,
                        "size": adjusted_size,
                        "sl": adjusted_sl,
                        "tp": adjusted_tp,
                        "calibration_id": calibration_id,
                        "signal_type": trade_signal.signal_type,
                        "atr": trade_signal.atr,
                        "open_time": time.time(),
                    }

                    ctx_builder.record_trade_time()

                    logger.info(
                        "trade_executed",
                        extra={
                            "order_id": order.order_id,
                            "side": side,
                            "size": contract_size,
                            "entry": trade_signal.entry_price,
                            "original_sl": trade_signal.stop_loss,
                            "adjusted_sl": adjusted_sl,
                            "original_tp": trade_signal.take_profit,
                            "adjusted_tp": adjusted_tp,
                            "original_size": position_size,
                            "adjusted_size": adjusted_size,
                            "risk_usd": balance * config["risk_per_trade"],
                            "ai_decision": ai_result.decision if ai_result else "disabled",
                            "ai_confidence": ai_result.confidence if ai_result else None,
                            "ai_market_regime": ai_result.market_regime if ai_result else None,
                            "calibration_id": calibration_id,
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
