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
from bot.data import add_indicators
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

    # Validate numeric ranges to prevent dangerous misconfigurations
    if not 1 <= config["leverage"] <= 25:
        raise ValueError(f"leverage must be 1-25, got {config['leverage']}")
    if not 0.001 <= config["risk_per_trade"] <= 0.1:
        raise ValueError(f"risk_per_trade must be 0.1%-10%, got {config['risk_per_trade']}")
    if not 0.005 <= config["max_daily_loss"] <= 0.5:
        raise ValueError(f"max_daily_loss must be 0.5%-50%, got {config['max_daily_loss']}")
    if config["atr_sl_mult"] <= 0 or config["atr_tp_mult"] <= 0:
        raise ValueError("atr_sl_mult and atr_tp_mult must be > 0")
    if config.get("atr_trail_mult", config["atr_sl_mult"]) <= 0:
        raise ValueError("atr_trail_mult must be > 0")

    # AI layer validation
    ai_cfg = config.get("ai_layer", {})
    if ai_cfg.get("enabled"):
        if ai_cfg.get("max_tokens", 1024) < 256:
            raise ValueError("ai max_tokens must be >= 256")
        if not 0.0 <= ai_cfg.get("confidence_threshold", 0.65) <= 1.0:
            raise ValueError("confidence_threshold must be 0-1")

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

    # Validate adjusted levels are in the correct direction
    if signal_type == SignalType.LONG:
        if adjusted_sl >= entry_price or adjusted_tp <= entry_price:
            logger.warning("ai_adjustments_invalid_direction_reverting")
            adjusted_sl = stop_loss
            adjusted_tp = take_profit
            adjusted_size = position_size
    elif signal_type == SignalType.SHORT:
        if adjusted_sl <= entry_price or adjusted_tp >= entry_price:
            logger.warning("ai_adjustments_invalid_direction_reverting")
            adjusted_sl = stop_loss
            adjusted_tp = take_profit
            adjusted_size = position_size

    return adjusted_size, adjusted_sl, adjusted_tp


def check_closed_positions(
    open_trade_ids: dict,
    current_positions: list,
    journal: TradeJournal,
    risk_mgr: RiskManager,
    calibration_tracker: Optional[CalibrationTracker],
    symbol: str,
    client=None,
    last_trade_close: Optional[dict] = None,
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

            # Record close time for cooldown tracking
            if last_trade_close is not None:
                last_trade_close[trade_side] = {
                    "time": time.time(),
                    "reason": close_reason,
                }

            if calibration_tracker and info.get("calibration_id"):
                outcome = "win" if estimated_pnl > 0 else "loss"
                # Item 5: compute default_pnl (what PnL would have been without AI)
                default_pnl = None
                orig_size = info.get("original_size")
                orig_sl = info.get("original_sl")
                orig_tp = info.get("original_tp")
                if orig_size is not None and orig_sl is not None and orig_tp is not None:
                    if trade_side == "long":
                        default_sl_pnl = (orig_sl - entry) / entry * orig_size
                        default_tp_pnl = (orig_tp - entry) / entry * orig_size
                    else:
                        default_sl_pnl = (entry - orig_sl) / entry * orig_size
                        default_tp_pnl = (entry - orig_tp) / entry * orig_size
                    default_pnl = default_tp_pnl if close_reason == "tp" else default_sl_pnl
                calibration_tracker.record_outcome(
                    info["calibration_id"], outcome, estimated_pnl,
                    default_pnl=default_pnl,
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


async def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for up to `seconds`, waking early if shutdown is requested.

    Returns:
        True if shutdown was requested (caller should break/return),
        False if the full sleep elapsed normally.
    """
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True  # Shutdown requested
    except asyncio.TimeoutError:
        return False  # Normal timeout — continue


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

    # Rebuild consecutive losses and daily PnL from database to survive restarts
    try:
        recent_pnls = journal.get_recent_results(limit=20)
        consecutive = 0
        for pnl in recent_pnls:  # Most recent first
            if pnl < 0:
                consecutive += 1
            else:
                break
        if consecutive > 0:
            risk_mgr.state.consecutive_losses = consecutive
            logger.info(
                "consecutive_losses_restored",
                extra={"count": consecutive},
            )
        # Restore today's PnL from database
        today_pnl = journal.get_daily_pnl()
        if today_pnl != 0:
            risk_mgr.state.daily_pnl = today_pnl
            logger.info("daily_pnl_restored", extra={"pnl": today_pnl})
    except Exception as e:
        logger.warning("state_restoration_failed", extra={"error": str(e)})

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
    # {trade_id: {side, entry_price, size, original_size, sl, tp, tp1_price, tp1_hit,
    #             calibration_id, signal_type, atr, regime, open_time}}
    tracked_trades: dict[int, dict] = {}

    # Track last trade close per side for cooldown (5B.1 Item 3)
    last_trade_close: dict[str, dict] = {}

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
                    # Item 6: Fetch fresh ATR for restored trades so trailing stop works
                    restored_atr = 0.0
                    try:
                        fresh_df = client.get_ohlcv(
                            config["symbol"], config["timeframe_signal"], limit=100
                        )
                        fresh_df = add_indicators(fresh_df, config)
                        if "atr" in fresh_df.columns and len(fresh_df) > 0:
                            restored_atr = float(fresh_df["atr"].iloc[-2])
                        logger.info("restored_trade_atr_fetched", extra={"trade_id": trade_id, "atr": restored_atr})
                    except Exception as atr_err:
                        logger.warning("restored_trade_atr_fetch_failed", extra={"trade_id": trade_id, "error": str(atr_err)})

                    size_usdt_restored = db_trade["size"] * db_trade["entry_price"]
                    tracked_trades[trade_id] = {
                        "side": db_trade["side"],
                        "entry_price": db_trade["entry_price"],
                        "size": size_usdt_restored,
                        "original_size": size_usdt_restored,
                        "sl": db_trade.get("stop_loss", 0),
                        "tp": db_trade.get("take_profit", 0),
                        "tp1_price": 0.0,
                        "tp1_hit": True,  # Mark as hit to avoid partial close on restart
                        "calibration_id": None,
                        "signal_type": SignalType.LONG if db_trade["side"] == "buy" else SignalType.SHORT,
                        "atr": restored_atr,
                        "regime": "trending",
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
                last_trade_close=last_trade_close,
            )

            # Update trailing stops for open positions
            if tracked_trades:
                try:
                    current_price = client.get_ticker_price(config["symbol"])

                    # Fetch current ATR for dynamic trailing (Item 6)
                    current_atr_df = None
                    try:
                        current_atr_df = client.get_ohlcv(
                            config["symbol"], config["timeframe_signal"], limit=30
                        )
                    except Exception as e:
                        logger.warning("trail_atr_fetch_failed", extra={"error": str(e)})

                    for trade_id, info in list(tracked_trades.items()):
                        # Skip trailing stop for restored trades without ATR data
                        if info["atr"] <= 0:
                            continue
                        sig_type = info["signal_type"]

                        # Item 6: Use current ATR instead of entry-time ATR
                        effective_atr = info["atr"]
                        if current_atr_df is not None and len(current_atr_df) >= 2:
                            try:
                                atr_df_with_ind = add_indicators(current_atr_df, config)
                                live_atr = atr_df_with_ind.iloc[-2].get("atr", None)
                                if live_atr and live_atr > 0:
                                    effective_atr = float(live_atr)
                            except Exception as e:
                                logger.warning("trail_atr_compute_failed", extra={"error": str(e)})

                        # Item 7: Regime-adaptive trail multiplier
                        regime = info.get("regime", "trending")
                        regime_trail_config = config.copy()
                        if regime == "ranging":
                            regime_trail_config["atr_trail_mult"] = config.get(
                                "atr_trail_mult_ranging", config.get("atr_trail_mult", 1.8)
                            )
                        elif regime == "volatile":
                            regime_trail_config["atr_trail_mult"] = config.get(
                                "atr_trail_mult_volatile", config.get("atr_trail_mult", 1.8)
                            )
                        else:
                            regime_trail_config["atr_trail_mult"] = config.get(
                                "atr_trail_mult_trending", config.get("atr_trail_mult", 1.8)
                            )

                        # Item 5: Partial TP1 check
                        partial_tp_enabled = config.get("partial_tp_enabled", False)
                        if partial_tp_enabled and not info.get("tp1_hit", True):
                            tp1_price = info.get("tp1_price", 0.0)
                            if tp1_price > 0:
                                tp1_triggered = (
                                    (sig_type == SignalType.LONG and current_price >= tp1_price)
                                    or (sig_type == SignalType.SHORT and current_price <= tp1_price)
                                )
                                if tp1_triggered:
                                    partial_pct = config.get("partial_tp_pct", 0.5)
                                    original_size_usdt = info.get("original_size", info["size"])
                                    partial_contracts = (original_size_usdt * partial_pct) / info["entry_price"]
                                    partial_contracts = round(partial_contracts, 6)

                                    partial_close_ok = False
                                    try:
                                        close_side = "sell" if info["side"] == "buy" else "buy"
                                        client.place_order(
                                            side=close_side,
                                            size=partial_contracts,
                                            reduce_only=True,
                                        )
                                        partial_close_ok = True
                                        logger.info(
                                            "partial_tp1_closed",
                                            extra={
                                                "trade_id": trade_id,
                                                "tp1_price": tp1_price,
                                                "current_price": current_price,
                                                "partial_contracts": partial_contracts,
                                            },
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            "partial_tp1_close_failed",
                                            extra={"trade_id": trade_id, "error": str(e)},
                                        )

                                    if partial_close_ok:
                                        info["tp1_hit"] = True
                                        info["size"] -= partial_contracts * info["entry_price"]
                                        info["size"] = max(info["size"], 0.0)

                                        if config.get("move_sl_to_be_after_tp1", True):
                                            breakeven = info["entry_price"]
                                            be_success = client.modify_sl(
                                                symbol=config["symbol"],
                                                side=info["side"],
                                                new_sl=round(breakeven, 2),
                                            )
                                            if be_success:
                                                info["sl"] = breakeven
                                                logger.info("sl_moved_to_breakeven", extra={"trade_id": trade_id, "breakeven": breakeven})
                                            else:
                                                logger.warning("sl_breakeven_move_failed", extra={"trade_id": trade_id})

                        # Trailing stop update
                        new_sl = compute_trailing_stop(
                            current_price, info["sl"], effective_atr,
                            sig_type, regime_trail_config,
                        )
                        if new_sl != info["sl"]:
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
                                        "atr": round(effective_atr, 4),
                                        "regime": regime,
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
                if await _interruptible_sleep(60):
                    break
                continue

            # Fetch data with error handling
            try:
                signal_df = client.get_ohlcv(
                    config["symbol"], config["timeframe_signal"], limit=100
                )
                # Trend TF needs more candles for EMA(50) to stabilize
                trend_limit = max(100, config.get("ema_trend", 50) * 3)
                trend_df = client.get_ohlcv(
                    config["symbol"], config["timeframe_trend"], limit=trend_limit
                )
                risk_mgr.clear_api_errors()
            except Exception as e:
                risk_mgr.record_api_error()
                logger.error("data_fetch_error", extra={"error": str(e)})
                if await _interruptible_sleep(30):
                    break
                continue

            # Generate signal (also returns enriched df with indicators for reuse)
            trade_signal, enriched_signal_df = generate_signal(signal_df, trend_df, config)

            if trade_signal is not None:
                # Item 1: Enforce 1 trade per side — skip if already tracking a trade on the same side
                new_side = "buy" if trade_signal.signal_type == SignalType.LONG else "sell"
                trade_side_label = "long" if new_side == "buy" else "short"
                already_open_side = any(info["side"] == new_side for info in tracked_trades.values())
                if already_open_side:
                    logger.info(
                        "trade_skipped_duplicate_side",
                        extra={"side": new_side, "reason": "already_tracking_trade_on_this_side"},
                    )

                # Item 3: Same-side cooldown after close
                elif trade_side_label in last_trade_close:
                    lc = last_trade_close[trade_side_label]
                    tf = config["timeframe_signal"]
                    candle_secs = (int(tf.replace("h", "")) * 3600) if "h" in tf else (int(tf.replace("m", "")) * 60)
                    cooldown_candles = config.get(
                        "cooldown_candles_after_sl" if lc.get("reason") == "stop_loss" else "cooldown_candles_after_close",
                        4,
                    )
                    elapsed = time.time() - lc["time"]
                    required = cooldown_candles * candle_secs
                    if elapsed < required:
                        logger.info(
                            "trade_skipped_cooldown",
                            extra={
                                "side": trade_side_label,
                                "elapsed_s": int(elapsed),
                                "required_s": required,
                                "reason": lc.get("reason", "close"),
                            },
                        )
                        already_open_side = True  # Reuse flag to skip trade

                else:
                    # Validate order through risk manager
                    approved, reason, position_size = risk_mgr.validate_order(
                        balance=balance,
                        entry_price=trade_signal.entry_price,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        num_open_positions=num_positions,
                        regime=trade_signal.regime,
                    )

                if not already_open_side and approved:
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
                        if await _interruptible_sleep(60):
                            break
                        continue

                    # Weekend / low-liquidity filter (Item 8)
                    now_utc = datetime.now(tz=timezone.utc)
                    if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
                        if not config.get("weekend_trading_enabled", True):
                            logger.info(
                                "trade_skipped_weekend",
                                extra={"day": now_utc.strftime("%A"), "reason": "weekend_trading_enabled=false"},
                            )
                            if await _interruptible_sleep(60):
                                break
                            continue
                        weekend_reduction = config.get("weekend_size_reduction", 0.5)
                        position_size *= weekend_reduction
                        logger.info(
                            "weekend_size_reduction_applied",
                            extra={
                                "day": now_utc.strftime("%A"),
                                "reduction_factor": weekend_reduction,
                                "new_size": round(position_size, 2),
                            },
                        )

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

                        # Build market context, passing pre-fetched DataFrames
                        # to avoid redundant OHLCV fetches and indicator recomputation
                        market_ctx = await ctx_builder.build(
                            config["symbol"],
                            risk_mgr.state,
                            signal_df=enriched_signal_df,
                            trend_df=trend_df,
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

                        # Item 2: Wire AI regime to risk manager
                        _regime_conservativeness = {
                            "trending": 0, "low_liquidity": 1,
                            "ranging": 2, "volatile": 3, "unknown": -1,
                        }
                        signal_regime = trade_signal.regime
                        ai_regime = ai_result.market_regime
                        signal_rank = _regime_conservativeness.get(signal_regime, 0)
                        ai_rank = _regime_conservativeness.get(ai_regime, -1)
                        if ai_rank > signal_rank:
                            logger.info(
                                "ai_regime_overrides_signal",
                                extra={
                                    "signal_regime": signal_regime,
                                    "ai_regime": ai_regime,
                                },
                            )
                            _, _, position_size = risk_mgr.validate_order(
                                balance=balance,
                                entry_price=trade_signal.entry_price,
                                stop_loss=trade_signal.stop_loss,
                                take_profit=trade_signal.take_profit,
                                num_open_positions=num_positions,
                                regime=ai_regime,
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
                            if await _interruptible_sleep(60):
                                break
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

                    # Verify SL was actually set on the position with retry
                    sl_verified = False
                    for sl_attempt in range(3):
                        try:
                            await asyncio.sleep(0.3 * (sl_attempt + 1))
                            verify_positions = client.get_positions()
                            for vp in verify_positions:
                                if vp.get("side") == ("long" if side == "buy" else "short"):
                                    sl_val = float(vp.get("stopLossPrice") or vp.get("info", {}).get("stopLoss", 0) or 0)
                                    if sl_val > 0:
                                        sl_verified = True
                                    break
                            if sl_verified:
                                break
                            logger.warning(
                                "sl_not_set_retrying",
                                extra={"attempt": sl_attempt + 1},
                            )
                            client.modify_sl(
                                symbol=config["symbol"],
                                side=side,
                                new_sl=round(adjusted_sl, 2),
                            )
                        except Exception as e:
                            logger.error(
                                "sl_verification_failed",
                                extra={"attempt": sl_attempt + 1, "error": str(e)},
                            )

                    if not sl_verified:
                        logger.critical(
                            "sl_verification_failed_closing_position",
                            extra={"order_id": order.order_id},
                        )
                        close_ok = False
                        try:
                            client.close_all_positions()
                            close_ok = True
                        except Exception as close_err:
                            logger.critical(
                                "sl_verification_and_close_failed_halting",
                                extra={"order_id": order.order_id, "error": str(close_err)},
                            )
                        if not close_ok:
                            # Cannot verify SL AND cannot close position — must halt immediately
                            shutdown_event.set()
                            break
                        if await _interruptible_sleep(60):
                            break
                        continue

                    # Use actual fill price and size, not requested
                    actual_entry = order.price if order.price else trade_signal.entry_price
                    actual_size = order.size  # Filled size (handles partial fills)

                    # Item 7: Recompute SL/TP from actual fill price, preserving ATR distances
                    sl_distance = abs(trade_signal.entry_price - adjusted_sl)
                    tp_distance = abs(adjusted_tp - trade_signal.entry_price)
                    if trade_signal.signal_type == SignalType.LONG:
                        fill_adjusted_sl = actual_entry - sl_distance
                        fill_adjusted_tp = actual_entry + tp_distance
                    else:
                        fill_adjusted_sl = actual_entry + sl_distance
                        fill_adjusted_tp = actual_entry - tp_distance
                    # Only update on exchange if fill price differed meaningfully
                    epsilon = trade_signal.entry_price * 0.0005  # 0.05% threshold
                    if abs(actual_entry - trade_signal.entry_price) > epsilon:
                        logger.info(
                            "sl_tp_recomputed_from_fill",
                            extra={
                                "requested_entry": trade_signal.entry_price,
                                "fill_entry": actual_entry,
                                "old_sl": round(adjusted_sl, 2),
                                "new_sl": round(fill_adjusted_sl, 2),
                                "old_tp": round(adjusted_tp, 2),
                                "new_tp": round(fill_adjusted_tp, 2),
                            },
                        )
                        client.modify_sl(
                            symbol=config["symbol"],
                            side=side,
                            new_sl=round(fill_adjusted_sl, 2),
                        )
                        adjusted_sl = fill_adjusted_sl
                        adjusted_tp = fill_adjusted_tp

                    # Log trade with AI advisor data
                    trade_id = journal.log_trade_open(
                        symbol=config["symbol"],
                        side=side,
                        entry_price=actual_entry,
                        size=actual_size,
                        stop_loss=adjusted_sl,
                        take_profit=adjusted_tp,
                        ai_decision=ai_result.decision if ai_result else None,
                        ai_confidence=ai_result.confidence if ai_result else None,
                        ai_reasoning=ai_result.reasoning if ai_result else None,
                        ai_risk_flags=ai_result.risk_flags if ai_result else None,
                        ai_override=False,
                    )

                    # Track for position monitoring (use actual filled values)
                    actual_size_usdt = actual_size * actual_entry

                    # Compute TP1 price for partial take-profit
                    partial_tp_atr_mult = config.get("partial_tp_atr_mult", 2.0)
                    if trade_signal.signal_type == SignalType.LONG:
                        tp1_price = actual_entry + partial_tp_atr_mult * trade_signal.atr
                    else:
                        tp1_price = actual_entry - partial_tp_atr_mult * trade_signal.atr

                    tracked_trades[trade_id] = {
                        "side": side,
                        "entry_price": actual_entry,
                        "size": actual_size_usdt,
                        "original_size": actual_size_usdt,
                        "sl": adjusted_sl,
                        "tp": adjusted_tp,
                        "tp1_price": tp1_price,
                        "tp1_hit": False,
                        "calibration_id": calibration_id,
                        "signal_type": trade_signal.signal_type,
                        "atr": trade_signal.atr,
                        "regime": trade_signal.regime,
                        "open_time": time.time(),
                        # Pre-AI values for value_add calibration (Item 5)
                        "original_sl": trade_signal.stop_loss,
                        "original_tp": trade_signal.take_profit,
                    }

                    ctx_builder.record_trade_time()

                    logger.info(
                        "trade_executed",
                        extra={
                            "order_id": order.order_id,
                            "side": side,
                            "size": actual_size,
                            "entry": actual_entry,
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
                elif not already_open_side:
                    logger.info("trade_rejected", extra={"reason": reason})

            # Wait until next candle close for timely signal detection
            now = datetime.now(tz=timezone.utc)
            tf = config["timeframe_signal"]
            if "h" in tf:
                signal_minutes = int(tf.replace("h", "")) * 60
            elif "m" in tf:
                signal_minutes = int(tf.replace("m", ""))
            else:
                signal_minutes = 15  # default
            minutes_until_close = (signal_minutes - 1) - (now.minute % signal_minutes)
            seconds_until_close = minutes_until_close * 60 + (60 - now.second)
            # Item 15: Write heartbeat file so deployment monitoring can detect a stalled bot
            heartbeat_path = Path(os.getenv("BOT_DATA_DIR", "data")) / "heartbeat"
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.write_text(str(time.time()))

            # Sleep until ~2s after candle close (give exchange time to finalize)
            # Item 5: Use shutdown-aware sleep so SIGTERM wakes the bot immediately
            sleep_time = max(10, min(seconds_until_close + 2, signal_minutes * 60))
            if await _interruptible_sleep(sleep_time):
                break

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

            if await _interruptible_sleep(30):
                break

    # Graceful shutdown: close all positions and cancel orders
    logger.warning("bot_shutting_down", extra={"tracked_trades": len(tracked_trades)})
    try:
        client.cancel_all_orders()
        remaining_positions = client.get_positions()
        if remaining_positions:
            logger.warning(
                "closing_positions_on_shutdown",
                extra={"count": len(remaining_positions)},
            )
            client.close_all_positions()
            # Record closures for tracked trades
            for trade_id, info in tracked_trades.items():
                try:
                    current_price = client.get_ticker_price(config["symbol"])
                    trade_side = "long" if info["side"] == "buy" else "short"
                    if trade_side == "long":
                        pnl = (current_price - info["entry_price"]) / info["entry_price"] * info["size"]
                    else:
                        pnl = (info["entry_price"] - current_price) / info["entry_price"] * info["size"]
                    pnl_pct = pnl / info["size"] * 100 if info["size"] > 0 else 0
                    duration = int(time.time() - info["open_time"])
                    journal.log_trade_close(
                        trade_id=trade_id,
                        exit_price=current_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        close_reason="graceful_shutdown",
                        duration_seconds=duration,
                    )
                    risk_mgr.record_trade_result(pnl)
                except Exception as close_err:
                    logger.error("shutdown_close_log_failed", extra={"error": str(close_err)})
    except Exception as e:
        logger.error("graceful_shutdown_failed", extra={"error": str(e)})

    logger.info("bot_stopped")

    # Close persistent database connections
    try:
        journal.close()
    except Exception:
        pass
    if calibration_tracker:
        try:
            calibration_tracker.close()
        except Exception:
            pass


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
