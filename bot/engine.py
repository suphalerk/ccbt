"""TradingEngine — encapsulates the main trading loop logic.

Extracted from main.py as a pure refactor.  All behaviour, log messages,
and edge-case handling are preserved exactly; only the structural boundary
has changed.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.ai_analyst import AIAnalyst, CandidateSignal
from bot.context_builder import ContextBuilder
from bot.data import add_indicators
from bot.exchange import BybitClient
from bot.logger import CalibrationTracker, TradeJournal
from bot.mode import BotMode, read_bot_mode
from bot.news_fetcher import NewsFetcher
from bot.risk import RiskManager
from bot.strategy import SignalType, compute_net_rr, compute_signal_quality_score, compute_trailing_stop, generate_signal
from bot.telegram import send_alert

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helper (previously in main.py at module scope)
# ---------------------------------------------------------------------------

def _infer_close_reason(
    exit_price: float,
    pnl: float,
    info: dict,
) -> str:
    """Infer a semantically correct close_reason for a stop exit.

    Classification uses pnl-sign as the structural top-priority discriminator:

    Priority ordering (evaluated top-to-bottom; first match wins):
    1. ``stop_loss``       — pnl < 0; hard loss regardless of exit proximity to TP or entry.
    2. ``tp``              — pnl >= 0 and exit is closer to TP than to SL.
    3. ``breakeven``       — pnl >= 0 and exit within 0.5% of entry (stop moved to ~entry).
    4. ``trail_stop``      — pnl > 0 and exit outside the entry band (ratcheted stop).

    The pnl-sign gate on step 1 ensures a genuine loss is never labelled
    ``breakeven``, even when the exit price falls within the breakeven band
    (e.g. Gold H1 with ATR SL of ~0.3% where the 0.5% band would otherwise
    mask the loss and use the shorter ``cooldown_candles_after_close`` instead
    of ``cooldown_candles_after_sl``).

    Classification is symmetric — the result does not depend on trade direction
    (long vs short).

    The function intentionally never returns the legacy ``sl`` label for new
    closes.  Old DB rows retain ``sl``/``tp`` — those are additive.

    Args:
        exit_price: Actual (or estimated) exit price.
        pnl:        Realised PnL in quote currency (positive = profit).
        info:       Tracked-trade dict for the position.  Must contain
                    ``entry_price``, ``sl`` (current, possibly ratcheted),
                    and ``tp``.

    Returns:
        One of ``"tp"``, ``"trail_stop"``, ``"breakeven"``, ``"stop_loss"``.
    """
    entry = info["entry_price"]
    tp = info["tp"]
    sl = info["sl"]  # current (possibly ratcheted) SL

    # 1. Negative PnL → hard stop loss; checked first so a fee-driven near-TP
    #    exit or a genuine loss inside the breakeven band is never mislabelled.
    if pnl < 0:
        return "stop_loss"

    # 2. TP hit (pnl >= 0): exit is closest to TP
    dist_to_tp = abs(exit_price - tp)
    dist_to_sl = abs(exit_price - sl)
    if dist_to_tp < dist_to_sl:
        return "tp"

    # 3–4 are stop exits with pnl >= 0.  Classify by exit-price proximity to entry.
    # Breakeven band: exit within 0.5% of entry price counts as near-zero PnL.
    breakeven_band = entry * 0.005
    exit_near_entry = abs(exit_price - entry) <= breakeven_band

    # Near entry (stop moved to ~entry) → breakeven
    if exit_near_entry:
        return "breakeven"

    # Positive PnL and outside entry band → trailing stop ratcheted into profit
    return "trail_stop"


def _candle_seconds(tf: str) -> int:
    """Return the number of seconds in one candle for a given timeframe string.

    Handles both lowercase (``"1h"``, ``"15m"``) and uppercase (``"H1"``, ``"4H"``)
    variants so Gold ``timeframe_signal="H1"`` is parsed correctly.

    Args:
        tf: Timeframe string from config, e.g. ``"15m"``, ``"1h"``, ``"4H"``, ``"H1"``.

    Returns:
        Duration in seconds.  Falls back to 900 (15 m) with a ``logger.warning``
        when the format is unrecognised so callers never get a silent wrong value.
    """
    tf_lower = tf.lower()
    if "h" in tf_lower:
        return int(tf_lower.replace("h", "")) * 3600
    if "m" in tf_lower:
        return int(tf_lower.replace("m", "")) * 60
    logger.warning(
        "candle_seconds_unknown_tf",
        extra={"tf": tf, "fallback_seconds": 900},
    )
    return 900  # safe 15-minute fallback


def check_closed_positions(
    open_trade_ids: dict,
    current_positions: list,
    journal: TradeJournal,
    risk_mgr: RiskManager,
    calibration_tracker: Optional[CalibrationTracker],
    symbol: str,
    client=None,
    last_trade_close: Optional[dict] = None,
    recently_closed: Optional[dict] = None,
) -> dict:
    """Detect positions closed by exchange (SL/TP fill) and update state.

    Compares tracked open trades against current exchange positions.
    When a tracked trade is no longer open on the exchange, it has been
    closed by SL or TP. Queries exchange for actual fill data to determine
    whether TP or SL was hit.

    Args:
        open_trade_ids: Dict of {trade_id: {side, entry_price, size, sl, tp,
            calibration_id, signal_type, atr, open_time}}.
        current_positions: Current positions from exchange.
        journal: Trade journal for logging closes.
        risk_mgr: Risk manager for recording PnL.
        calibration_tracker: Optional calibration tracker.
        symbol: Trading symbol.
        client: BybitClient instance for fetching actual trade data.
        last_trade_close: Optional dict tracking last close per side.
        recently_closed: Optional shared dict keyed by normalised symbol used
            to deduplicate close events when several config-bots share one
            netted exchange position.  Pass the SAME dict object to all bots
            running the same symbol.  When a symbol is already recorded here
            the second (and further) bots skip journaling and alerting but
            still remove the trade_id from their local open_trade_ids.

    Returns:
        Updated open_trade_ids dict with closed trades removed.
    """
    # Maximum ratio of exit_price / entry_price that is physically plausible
    # within a single candle for any perpetual futures contract.  A value
    # outside (entry/MAX, entry*MAX) is treated as a cross-symbol contamination
    # or stale fill and rejected.  At 5x this still allows for genuine
    # 5-bagger candles (extremely rare but possible for micro-caps) while
    # catching POLUSDT (0.09) being matched against an AXS (2.5) fill.
    _MAX_EXIT_RATIO = 5.0

    # TTL for recently_closed entries: after this window (seconds) the symbol
    # is allowed to journal + alert again so a genuine subsequent trade can
    # be reported.  60 s is long enough to cover all config-bots processing the
    # same candle tick (they all run within a few seconds of each other) but
    # short enough that a re-entry and re-close on the same symbol 5+ minutes
    # later is not suppressed.
    _RECENTLY_CLOSED_TTL = 60.0

    if not open_trade_ids:
        return open_trade_ids

    # Purge stale recently_closed entries before processing this batch.
    if recently_closed is not None:
        _now = time.time()
        _stale = [k for k, v in recently_closed.items() if _now - v > _RECENTLY_CLOSED_TTL]
        for k in _stale:
            del recently_closed[k]

    # Determine which sides have active positions on exchange
    active_sides: set[str] = set()

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
                    # Find the closing trade: opposite side to our entry,
                    # AND price must be within a sane ratio of entry.
                    # Rationale: fetch_my_trades is called for the correct
                    # symbol but exchange APIs occasionally return fills from
                    # other symbols in the same batch (cross-symbol
                    # contamination).  Filtering by price ratio catches
                    # cases like POLUSDT entry=0.09 matched against an AXS
                    # fill at 2.5 (28x — physically impossible in one candle).
                    close_side = "sell" if info["side"] == "buy" else "buy"
                    if entry > 0:
                        _price_lo = entry / _MAX_EXIT_RATIO
                        _price_hi = entry * _MAX_EXIT_RATIO
                    else:
                        _price_lo = 0.0
                        _price_hi = float("inf")
                    for t in reversed(recent_trades):
                        if t["side"] == close_side and t["amount"] > 0:
                            candidate_price = float(t["price"])
                            if not (_price_lo <= candidate_price <= _price_hi):
                                logger.warning(
                                    "exit_price_out_of_range_rejected",
                                    extra={
                                        "symbol": symbol,
                                        "entry": entry,
                                        "candidate_exit": candidate_price,
                                        "ratio": candidate_price / entry if entry > 0 else None,
                                    },
                                )
                                continue  # skip this fill; try next candidate
                            exit_price = candidate_price
                            # Calculate actual PnL from fill price
                            if trade_side == "long":
                                actual_pnl = (exit_price - entry) / entry * info["size"]
                            else:
                                actual_pnl = (entry - exit_price) / entry * info["size"]
                            break
                except Exception as e:
                    logger.warning("failed_to_fetch_actual_pnl", extra={"error": str(e)})

            if actual_pnl is not None and exit_price is not None:
                estimated_pnl = actual_pnl
                close_reason = _infer_close_reason(
                    exit_price=exit_price,
                    pnl=estimated_pnl,
                    info=info,
                )
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
                            else:
                                estimated_pnl = sl_pnl
                                exit_price = sl
                        else:
                            if current_price <= tp:
                                estimated_pnl = tp_pnl
                                exit_price = tp
                            else:
                                estimated_pnl = sl_pnl
                                exit_price = sl
                    except Exception:
                        estimated_pnl = sl_pnl
                        exit_price = sl
                else:
                    estimated_pnl = sl_pnl
                    exit_price = sl
                close_reason = _infer_close_reason(
                    exit_price=exit_price,
                    pnl=estimated_pnl,
                    info=info,
                )

            pnl_pct = estimated_pnl / info["size"] * 100 if info["size"] > 0 else 0

            # ------------------------------------------------------------------
            # Multi-bot dedup: several config-bots share one netted exchange
            # position per symbol (e.g. AXS has 7 bots).  When the netted
            # position disappears each bot independently detects the absence and
            # would normally journal + alert separately.  If a shared
            # recently_closed registry is provided, the FIRST bot to reach this
            # point owns the journal write + alert; subsequent bots for the same
            # symbol within the same close window only remove their trade_id
            # from open_trade_ids (silent dedup — no duplicate DB row, no
            # duplicate Telegram alert).
            # ------------------------------------------------------------------
            is_primary_closer = True  # default: no shared registry
            _close_key: Optional[str] = None
            if recently_closed is not None:
                _close_key = norm_symbol
                if _close_key in recently_closed:
                    # Another bot already handled the journal + alert.
                    is_primary_closer = False
                    logger.info(
                        "close_deduped_secondary_bot",
                        extra={
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "dedup_key": _close_key,
                        },
                    )
                else:
                    # Do NOT mark the registry here — the journal write has not
                    # succeeded yet.  If log_trade_close raises (e.g. SQLite
                    # locked) and we marked early, a sibling/retry bot would
                    # see is_primary_closer=False and skip the journal entirely,
                    # leaving the DB row stuck at status='open' and the loss
                    # invisible to circuit breakers.  The mark is deferred to
                    # the success path below.
                    pass

            if is_primary_closer:
                # Crash-safe close sequence: log first; only proceed (and mark as
                # closed) when the journal write succeeds.  If log_trade_close raises
                # (e.g. SQLite locked), the trade stays in open_trade_ids so the next
                # loop iteration retries — preventing both silent data loss and the
                # double-count that would result from record_trade_result running
                # twice on the same position.
                try:
                    journal.log_trade_close(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        pnl=estimated_pnl,
                        pnl_pct=pnl_pct,
                        close_reason=close_reason,
                        duration_seconds=duration,
                    )
                except Exception as log_err:
                    logger.warning(
                        "trade_close_log_failed",
                        extra={"trade_id": trade_id, "error": str(log_err)},
                    )
                    continue  # do NOT call record_trade_result or closed.append
                    # NOTE: recently_closed is NOT yet marked, so the next loop
                    # iteration (or a sibling bot) can still claim primary ownership
                    # and retry the journal write.

                risk_mgr.record_trade_result(estimated_pnl)

                # Mark the registry NOW — journal + risk accounting both succeeded.
                # Sibling bots arriving after this point will see is_primary_closer=False
                # and skip the duplicate write correctly.
                if recently_closed is not None and _close_key is not None:
                    recently_closed[_close_key] = time.time()

            # STRUCTURAL double-count guard: mark as closed IMMEDIATELY after
            # risk accounting succeeds (or after dedup skip), BEFORE any
            # telemetry that might raise.  A future raising line in
            # calibration / logger.info / send_alert cannot re-drive
            # record_trade_result on the next loop iteration.
            closed.append(trade_id)

            # Record close time for cooldown tracking
            if last_trade_close is not None:
                last_trade_close[trade_side] = {
                    "time": time.time(),
                    "reason": close_reason,
                }

            if not is_primary_closer:
                # Secondary bot: trade_id removed, no further telemetry.
                continue

            # --- Telemetry (non-fatal: each block is independently guarded) ---
            try:
                if calibration_tracker and info.get("calibration_id"):
                    outcome = "win" if estimated_pnl > 0 else "loss"
                    # Compute default_pnl (what PnL would have been without AI)
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
            except Exception as calib_err:
                logger.warning(
                    "calibration_record_failed",
                    extra={"trade_id": trade_id, "error": str(calib_err)},
                )

            try:
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
                pnl_emoji = "✅" if estimated_pnl > 0 else "❌"
                send_alert(
                    f"{pnl_emoji} <b>{symbol}</b> {info['side'].upper()} closed ({close_reason})\n"
                    f"PnL: ${estimated_pnl:+,.2f} ({pnl_pct:+.1f}%)\n"
                    f"Duration: {duration//60}m"
                )
            except Exception as log_alert_err:
                logger.warning(
                    "trade_close_telemetry_failed",
                    extra={"trade_id": trade_id, "error": str(log_alert_err)},
                )

    # Cancel orphaned algo orders (e.g. TP remaining after SL trigger, or vice
    # versa).  On Binance, algo orders live in a separate book and persist even
    # after the position is gone — they would fire on the next same-direction
    # position if not cleaned up.
    if closed and client is not None:
        try:
            client.cancel_all_orders(symbol)
            logger.info(
                "orphaned_orders_cleaned",
                extra={"symbol": symbol, "closed_trades": len(closed)},
            )
        except Exception as e:
            logger.warning("orphaned_order_cleanup_failed", extra={"error": str(e)})

    for tid in closed:
        del open_trade_ids[tid]

    return open_trade_ids


def _apply_ai_adjustments(
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


# ---------------------------------------------------------------------------
# T3: Positions-fetch gate helper
# ---------------------------------------------------------------------------

def _should_skip_positions_fetch(
    tracked_trades: dict,
    portfolio_manager,
    flag: Optional[str],
) -> bool:
    """Return True iff the routine positions fetch at the top of the trading
    loop can be safely skipped.

    Gate is active ONLY when ``CCBT_SHARED_MARKETDATA == '1'`` (flag == '1').
    When the gate is inactive the function always returns False so the caller
    performs an unconditional ``get_positions()`` — byte-for-byte today.

    Gate logic (flag ON):
    - A bot WITH any tracked trade ALWAYS fetches  (return False).
    - A bot with NO tracked trades AND global cap full → skip (return True).
    - A bot with NO tracked trades AND cap not full → fetch (return False).
    - portfolio_manager is None (single-bot / main.py) → always fetch.
    """
    if flag != "1":
        # Flag OFF: unconditional fetch (parity with pre-T3 behaviour).
        return False

    # Evaluate cheapest predicate first: does this bot hold any tracked trade?
    if tracked_trades:
        # Holder always fetches — needed for SL/TP/monitor paths.
        return False

    # No tracked trade.  If there is no portfolio manager there is no global
    # cap concept → fetch anyway (single-bot path is unaffected).
    if portfolio_manager is None:
        return False

    # Skip iff the global cap is already full; otherwise fetch so the bot can
    # attempt to open a position.
    return portfolio_manager.open_count >= portfolio_manager.max_positions


# ---------------------------------------------------------------------------
# TradingEngine class
# ---------------------------------------------------------------------------

class TradingEngine:
    """Encapsulates the main trading loop as a cohesive class.

    All logic is a direct extraction of trading_loop() from main.py.
    No behaviour has been changed.

    Args:
        config: Bot configuration dict.
        shutdown_event: asyncio.Event set by signal handlers to trigger
            graceful shutdown.
        shared_exchange: Optional pre-built ccxt exchange instance (already
            has load_markets() called).  Passed through to BybitClient to
            skip the expensive per-bot exchange initialisation in multi-bot
            mode.  See bot.shared_exchange_pool.get_shared_exchange().
    """

    def __init__(
        self,
        config: dict,
        shutdown_event: asyncio.Event,
        shared_exchange=None,
        portfolio_manager=None,
        market_data=None,
        recently_closed: Optional[dict] = None,
    ) -> None:
        self._config = config
        self._shutdown_event = shutdown_event
        self._shared_exchange = shared_exchange
        self._portfolio_manager = portfolio_manager  # Optional global position limit
        self._market_data = market_data  # Optional SharedMarketData (T4)
        # Shared close-dedup registry (multi-bot: same dict for all bots sharing
        # a symbol so only one bot journals + alerts per netted-position close).
        self._recently_closed = recently_closed

        # Components (constructed in run() after initial balance fetch)
        self._client: Optional[BybitClient] = None
        self._risk_mgr: Optional[RiskManager] = None
        self._journal: Optional[TradeJournal] = None
        self._calibration_tracker: Optional[CalibrationTracker] = None
        self._ctx_builder: Optional[ContextBuilder] = None
        self._ai_analyst: Optional[AIAnalyst] = None
        self._news_fetcher: Optional[NewsFetcher] = None

        # Runtime state
        self._tracked_trades: dict[int, dict] = {}
        self._last_trade_close: dict[str, dict] = {}
        self._last_daily_reset = datetime.now(tz=timezone.utc).date()
        self._balance: float = 0.0
        self._ai_enabled: bool = False
        self._log_all_decisions: bool = True

        # Symbol clean for heartbeat and mode files
        self._symbol_clean = config["symbol"].replace("/", "").replace(":", "")
        self._current_mode = BotMode.NORMAL
        self._loop_count: int = 0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop: initialise components, then iterate until shutdown."""
        config = self._config

        # --- Build components ---
        self._client = BybitClient(
            config,
            shared_exchange=self._shared_exchange,
            market_data=self._market_data,
        )
        self._balance = self._client.get_balance()
        self._risk_mgr = RiskManager(config, self._balance)
        self._journal = TradeJournal()

        # Rebuild consecutive losses and daily PnL from database to survive restarts
        try:
            recent_pnls = self._journal.get_recent_results(limit=20)
            consecutive = 0
            for pnl in recent_pnls:  # Most recent first
                if pnl < 0:
                    consecutive += 1
                else:
                    break
            if consecutive > 0:
                self._risk_mgr.state.consecutive_losses = consecutive
                logger.info(
                    "consecutive_losses_restored",
                    extra={"count": consecutive},
                )
            # Restore today's PnL from database
            today_pnl = self._journal.get_daily_pnl()
            if today_pnl != 0:
                self._risk_mgr.state.daily_pnl = today_pnl
                logger.info("daily_pnl_restored", extra={"pnl": today_pnl})
        except Exception as e:
            logger.warning("state_restoration_failed", extra={"error": str(e)})

        # --- AI layer ---
        ai_config = config.get("ai_layer", {})
        self._ai_enabled = ai_config.get("enabled", False)

        self._news_fetcher = NewsFetcher(
            source=ai_config.get("news_source", "rss"),
            cryptopanic_token=os.getenv("CRYPTOPANIC_TOKEN"),
        )

        self._calibration_tracker = CalibrationTracker() if self._ai_enabled else None

        self._ctx_builder = ContextBuilder(
            self._client, config, self._news_fetcher, trade_journal=self._journal
        )

        self._ai_analyst = AIAnalyst(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            confidence_threshold=ai_config.get("confidence_threshold", 0.65),
            model=ai_config.get("model", "claude-sonnet-4-6"),
            max_tokens=ai_config.get("max_tokens", 1024),
            timeout_seconds=ai_config.get("timeout_seconds", 10.0),
            fallback_on_timeout=ai_config.get("fallback_on_timeout", "execute"),
            enabled=self._ai_enabled,
            calibration_tracker=self._calibration_tracker,
        )

        self._log_all_decisions = ai_config.get("log_all_decisions", True)

        # Set leverage (capture actual in case exchange auto-reduces)
        actual_leverage = self._client.set_leverage(config["leverage"])
        if actual_leverage and actual_leverage != config["leverage"]:
            logger.warning(
                "leverage_adjusted",
                extra={
                    "requested": config["leverage"],
                    "actual": actual_leverage,
                },
            )
            config["leverage"] = actual_leverage

        log_level = "warning" if not config.get("use_testnet", True) else "info"
        logger.log(
            logging.WARNING if log_level == "warning" else logging.INFO,
            "bot_started",
            extra={
                "symbol": config["symbol"],
                "balance": self._balance,
                "leverage": config["leverage"],
                "mode": "LIVE" if not config.get("use_testnet", True) else "testnet",
                "ai_layer_enabled": self._ai_enabled,
                "ai_mode": "advisor" if self._ai_enabled else "disabled",
            },
        )
        # Note: per-bot start alert removed to avoid 167 messages on startup.
        # main_multi.py sends a single summary alert instead.

        # Restore any positions already open on exchange (e.g. after restart)
        await self._restore_positions()

        # --- Main loop ---
        while not self._shutdown_event.is_set():
            try:
                self._loop_count += 1

                # Check bot mode (filesystem-based control from dashboard)
                new_mode = read_bot_mode(self._symbol_clean)
                if new_mode != self._current_mode:
                    logger.warning(
                        "bot_mode_changed",
                        extra={
                            "from": self._current_mode.value,
                            "to": new_mode.value,
                            "symbol": config["symbol"],
                        },
                    )
                    self._current_mode = new_mode

                # Handle PANIC mode — close everything immediately
                if self._current_mode == BotMode.PANIC:
                    logger.critical("panic_mode_activated", extra={"symbol": config["symbol"]})
                    send_alert(f"🚨 <b>{config['symbol']}</b> PANIC MODE — closing all positions!")
                    try:
                        self._client.cancel_all_orders()
                        self._client.close_all_positions()
                        # Log panic closures for tracked trades
                        for trade_id, info in self._tracked_trades.items():
                            try:
                                current_price = self._client.get_ticker_price(config["symbol"])
                                trade_side = "long" if info["side"] == "buy" else "short"
                                if trade_side == "long":
                                    pnl = (current_price - info["entry_price"]) / info["entry_price"] * info["size"]
                                else:
                                    pnl = (info["entry_price"] - current_price) / info["entry_price"] * info["size"]
                                pnl_pct = pnl / info["size"] * 100 if info["size"] > 0 else 0
                                self._journal.log_trade_close(
                                    trade_id=trade_id,
                                    exit_price=current_price,
                                    pnl=pnl,
                                    pnl_pct=pnl_pct,
                                    close_reason="panic_mode",
                                    duration_seconds=int(time.time() - info["open_time"]),
                                )
                                self._risk_mgr.record_trade_result(pnl)
                            except Exception:
                                pass
                    except Exception as e:
                        logger.error("panic_close_failed", extra={"error": str(e)})
                    break  # Exit main loop

                await self._daily_reset()

                # GRACEFUL_STOP: manage existing positions only, no new entries.
                # Exit when no tracked trades remain.
                if self._current_mode == BotMode.GRACEFUL_STOP:
                    positions = self._client.get_positions()
                    await self._monitor_positions(positions)
                    await self._update_trailing_stops()
                    if not self._tracked_trades:
                        logger.info("graceful_stop_complete", extra={"symbol": config["symbol"]})
                        break
                    if await self._sleep_until_next_candle():
                        break
                    continue  # Skip signal evaluation

                # TP_ONLY: no new entries, no trailing stops; only TP closes positions.
                # Also cancels open SL (stop-market) algo orders so only the TP remains.
                if self._current_mode == BotMode.TP_ONLY:
                    positions = self._client.get_positions()
                    await self._monitor_positions(positions)
                    # Cancel SL orders, keep only TP
                    if self._client._exchange_name == "binance":
                        try:
                            algo_orders = self._client._get_binance_algo_orders(config["symbol"])
                            market = self._client.exchange.market(config["symbol"])
                            for ao in algo_orders:
                                if str(ao.get("orderType", "")).upper() in ("STOP_MARKET", "STOP"):
                                    algo_id = ao.get("algoId")
                                    if algo_id:
                                        try:
                                            self._client._retry(
                                                self._client.exchange.fapiPrivateDeleteAlgoOrder,
                                                {"symbol": market["id"], "algoId": str(algo_id)},
                                            )
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                    if not self._tracked_trades:
                        logger.info("tp_only_complete", extra={"symbol": config["symbol"]})
                        break
                    if await self._sleep_until_next_candle():
                        break
                    continue

                # Get current positions
                # T3: flag-guarded gate — skip per-symbol API call when this
                # bot has no tracked trades and the global cap is already full
                # (a no-trade bot can't act anyway).  Holders ALWAYS fetch.
                if _should_skip_positions_fetch(
                    tracked_trades=self._tracked_trades,
                    portfolio_manager=self._portfolio_manager,
                    flag=os.environ.get("CCBT_SHARED_MARKETDATA"),
                ):
                    positions = []
                else:
                    positions = self._client.get_positions()
                num_positions = len(positions)

                # Check for positions closed by exchange (SL/TP fills)
                await self._monitor_positions(positions)

                # Update trailing stops for open positions
                await self._update_trailing_stops()

                # Evaluate new signal and execute if appropriate
                should_break = await self._evaluate_and_execute(
                    positions=positions,
                    num_positions=num_positions,
                )
                if should_break:
                    break

                # Sleep until next candle
                if await self._sleep_until_next_candle():
                    break

            except KeyboardInterrupt:
                break
            except Exception as e:
                should_halt = await self._handle_error(e)
                if should_halt:
                    break

        # Graceful shutdown
        await self._shutdown()

    # ------------------------------------------------------------------
    # State restoration (startup)
    # ------------------------------------------------------------------

    async def _restore_positions(self) -> None:
        """Restore tracking for positions already open on exchange after restart.

        Only restores ONE trade per side to avoid double-counting PnL
        (Bybit merges same-direction positions into one net position).
        Also registers restored positions with the PortfolioManager so
        the global position cap and duplicate-coin gate stay accurate.
        """
        config = self._config
        try:
            existing_positions = self._client.get_positions()
            open_db_trades = self._journal.get_open_trades()
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
                        # Fetch fresh ATR for restored trades so trailing stop works
                        restored_atr = 0.0
                        try:
                            fresh_df = self._client.get_ohlcv(
                                config["symbol"], config["timeframe_signal"], limit=100
                            )
                            fresh_df = add_indicators(fresh_df, config)
                            if "atr" in fresh_df.columns and len(fresh_df) > 0:
                                restored_atr = float(fresh_df["atr"].iloc[-2])
                            logger.info(
                                "restored_trade_atr_fetched",
                                extra={"trade_id": trade_id, "atr": restored_atr},
                            )
                        except Exception as atr_err:
                            logger.warning(
                                "restored_trade_atr_fetch_failed",
                                extra={"trade_id": trade_id, "error": str(atr_err)},
                            )

                        size_usdt_restored = db_trade["size"] * db_trade["entry_price"]
                        restored_sl = db_trade.get("stop_loss", 0)
                        self._tracked_trades[trade_id] = {
                            "side": db_trade["side"],
                            "entry_price": db_trade["entry_price"],
                            "size": size_usdt_restored,
                            "original_size": size_usdt_restored,
                            "sl": restored_sl,
                            "tp": db_trade.get("take_profit", 0),
                            "tp1_price": 0.0,
                            "tp1_hit": True,  # Mark as hit to avoid partial close on restart
                            "calibration_id": None,
                            "signal_type": (
                                SignalType.LONG if db_trade["side"] == "buy" else SignalType.SHORT
                            ),
                            "atr": restored_atr,
                            "regime": "trending",
                            "open_time": time.time(),
                        }
                        restored_sides.add(trade_side)
                        # Register with portfolio manager so global limits are accurate
                        if self._portfolio_manager is not None:
                            await self._portfolio_manager.register_open(config["symbol"])
                        logger.info(
                            "restored_tracked_trade",
                            extra={"trade_id": trade_id, "side": db_trade["side"]},
                        )
                        break
        except Exception as e:
            logger.warning("failed_to_restore_tracked_trades", extra={"error": str(e)})

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    async def _daily_reset(self) -> None:
        """Reset risk manager on UTC date change."""
        today = datetime.now(tz=timezone.utc).date()
        if today != self._last_daily_reset:
            yesterday_pnl = self._risk_mgr.state.daily_pnl
            self._balance = self._client.get_balance()
            self._risk_mgr.reset_daily(self._balance)
            self._last_daily_reset = today
            logger.info("daily_reset_triggered", extra={"date": str(today)})
            # Note: per-bot daily alert removed to avoid 167 messages.
            # Use /pnl command in Telegram for daily summary.

    # ------------------------------------------------------------------
    # Position monitoring
    # ------------------------------------------------------------------

    async def _monitor_positions(self, positions: list) -> None:
        """Detect positions closed by exchange (SL/TP fills) and update state.

        Args:
            positions: Current positions list from exchange.
        """
        trades_before = set(self._tracked_trades.keys())
        self._tracked_trades = check_closed_positions(
            open_trade_ids=self._tracked_trades,
            current_positions=positions,
            journal=self._journal,
            risk_mgr=self._risk_mgr,
            calibration_tracker=self._calibration_tracker,
            symbol=self._config["symbol"],
            client=self._client,
            last_trade_close=self._last_trade_close,
            recently_closed=self._recently_closed,
        )
        # Notify portfolio manager when positions are closed by exchange (SL/TP)
        if self._portfolio_manager is not None:
            trades_after = set(self._tracked_trades.keys())
            closed_count = len(trades_before) - len(trades_after)
            for _ in range(closed_count):
                await self._portfolio_manager.register_close(self._config["symbol"])

    # ------------------------------------------------------------------
    # Trailing stops
    # ------------------------------------------------------------------

    async def _update_trailing_stops(self) -> None:
        """Update trailing stops for all tracked open positions."""
        if not self._tracked_trades:
            return

        config = self._config
        try:
            current_price = self._client.get_ticker_price(config["symbol"])

            # Fetch current ATR for dynamic trailing
            current_atr_df = None
            try:
                current_atr_df = self._client.get_ohlcv(
                    config["symbol"], config["timeframe_signal"], limit=30
                )
            except Exception as e:
                logger.warning("trail_atr_fetch_failed", extra={"error": str(e)})

            for trade_id, info in list(self._tracked_trades.items()):
                # Skip trailing stop for restored trades without ATR data
                if info["atr"] <= 0:
                    continue
                sig_type = info["signal_type"]

                # Use current ATR instead of entry-time ATR
                effective_atr = info["atr"]
                if current_atr_df is not None and len(current_atr_df) >= 2:
                    try:
                        atr_df_with_ind = add_indicators(current_atr_df, config)
                        live_atr = atr_df_with_ind.iloc[-2].get("atr", None)
                        if live_atr and live_atr > 0:
                            effective_atr = float(live_atr)
                    except Exception as e:
                        logger.warning("trail_atr_compute_failed", extra={"error": str(e)})

                # Regime-adaptive trail multiplier
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

                # Partial TP1 check
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
                                self._client.place_order(
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
                                    be_buffer = config.get("breakeven_buffer_atr_mult", 0.0)
                                    buffer = be_buffer * effective_atr
                                    if info["side"] == "buy":
                                        breakeven = info["entry_price"] + buffer
                                    else:
                                        breakeven = info["entry_price"] - buffer
                                    be_success = self._client.modify_sl(
                                        symbol=config["symbol"],
                                        side=info["side"],
                                        new_sl=self._client.price_precision(breakeven),
                                    )
                                    if be_success:
                                        info["sl"] = breakeven
                                        logger.info(
                                            "sl_moved_to_breakeven",
                                            extra={"trade_id": trade_id, "breakeven": breakeven},
                                        )
                                    else:
                                        logger.warning(
                                            "sl_breakeven_move_failed",
                                            extra={"trade_id": trade_id},
                                        )

                # Trailing stop update (use wider trail after TP1)
                new_sl = compute_trailing_stop(
                    current_price, info["sl"], effective_atr,
                    sig_type, regime_trail_config,
                    post_tp1=info.get("tp1_hit", False),
                )
                if new_sl != info["sl"]:
                    old_sl = info["sl"]
                    success = self._client.modify_sl(
                        symbol=config["symbol"],
                        side=info["side"],
                        new_sl=self._client.price_precision(new_sl),
                    )
                    if success:
                        info["sl"] = new_sl
                        logger.info(
                            "trailing_stop_updated",
                            extra={
                                "trade_id": trade_id,
                                "old_sl": self._client.price_precision(old_sl),
                                "new_sl": self._client.price_precision(new_sl),
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

    # ------------------------------------------------------------------
    # Signal evaluation and order execution
    # ------------------------------------------------------------------

    async def _evaluate_and_execute(
        self,
        positions: list,
        num_positions: int,
    ) -> bool:
        """Fetch OHLCV data, generate signal, apply AI, place order if valid.

        Args:
            positions: Current exchange positions (already fetched this cycle).
            num_positions: Count of currently open positions.

        Returns:
            True if the main loop should break (shutdown triggered), else False.
        """
        config = self._config

        # Check if we can trade
        self._balance = self._client.get_balance()
        can_trade, reason = self._risk_mgr.can_trade(self._balance, num_positions)

        if not can_trade:
            logger.info("trade_skipped", extra={"reason": reason})
            if await self._interruptible_sleep(60):
                return True
            return False

        # Fetch OHLCV data
        try:
            signal_df = self._client.get_ohlcv(
                config["symbol"], config["timeframe_signal"], limit=100
            )
            # Trend TF needs more candles for EMA(50) to stabilise
            trend_limit = max(100, config.get("ema_trend", 50) * 3)
            trend_df = self._client.get_ohlcv(
                config["symbol"], config["timeframe_trend"], limit=trend_limit
            )
            self._risk_mgr.clear_api_errors()
        except Exception as e:
            self._risk_mgr.record_api_error()
            logger.error("data_fetch_error", extra={"error": str(e)})
            if await self._interruptible_sleep(30):
                return True
            return False

        # Generate signal (also returns enriched df with indicators for reuse)
        trade_signal, enriched_signal_df = generate_signal(signal_df, trend_df, config)

        # N6: persist recent candles + indicators to bot_ohlcv so the dashboard
        # can render the candle chart without hitting the exchange.
        # This reuses the DataFrame already produced above — NO new fetch.
        # Uses the journal's persistent connection (no new sqlite3.connect per tick).
        try:
            self._journal.upsert_candles(
                symbol=config["symbol"],
                timeframe=config["timeframe_signal"],
                df=enriched_signal_df,
            )
        except Exception as _candle_err:
            logger.warning(
                "candle_persist_failed",
                extra={"error": str(_candle_err)},
            )

        if trade_signal is None:
            return False

        # Trading hours filter — skip entries during low-liquidity dead hours
        trading_hours = config.get("trading_hours", {})
        if trading_hours.get("enabled", False):
            current_hour = datetime.now(timezone.utc).hour
            start_hour = trading_hours.get("start_utc", 0)
            end_hour = trading_hours.get("end_utc", 24)
            if start_hour < end_hour:
                if not (start_hour <= current_hour < end_hour):
                    logger.info(
                        "trade_skipped_trading_hours",
                        extra={"hour_utc": current_hour, "start": start_hour, "end": end_hour},
                    )
                    return False
            else:  # Wraps around midnight
                if end_hour <= current_hour < start_hour:
                    logger.info(
                        "trade_skipped_trading_hours",
                        extra={"hour_utc": current_hour, "start": start_hour, "end": end_hour},
                    )
                    return False

        # --- Duplicate side check ---
        new_side = "buy" if trade_signal.signal_type == SignalType.LONG else "sell"
        trade_side_label = "long" if new_side == "buy" else "short"
        already_open_side = any(
            info["side"] == new_side for info in self._tracked_trades.values()
        )
        if already_open_side:
            logger.info(
                "trade_skipped_duplicate_side",
                extra={"side": new_side, "reason": "already_tracking_trade_on_this_side"},
            )

        # --- Same-side cooldown after close ---
        if not already_open_side and trade_side_label in self._last_trade_close:
            lc = self._last_trade_close[trade_side_label]
            candle_secs = _candle_seconds(config["timeframe_signal"])
            # All stop exits use the after_sl cooldown: includes legacy "sl",
            # "stop_loss" (hard loss), "trail_stop" (ratcheted-stop profit), and
            # "breakeven" (stop moved to entry).  Only TP exits use after_close.
            # This preserves pre-taxonomy entry timing: before _infer_close_reason
            # was introduced all stop exits were labelled "sl" and always selected
            # the longer after_sl candle count.
            lc_reason = lc.get("reason", "close")
            is_sl = lc_reason in ("sl", "stop_loss", "trail_stop", "breakeven")
            cooldown_candles = config.get(
                "cooldown_candles_after_sl" if is_sl else "cooldown_candles_after_close",
                4,
            )
            elapsed = time.time() - lc["time"]
            required = cooldown_candles * candle_secs
            if elapsed < required:
                # Check flexible cooldown override
                flex_cfg = config.get("flexible_cooldown", {})
                override_applied = False
                if flex_cfg.get("enabled", False):
                    reduction = max(0.0, min(flex_cfg.get("cooldown_reduction_factor", 0.5), 1.0))
                    reduced_required = required * reduction
                    if elapsed >= reduced_required:
                        # Compute signal quality score
                        signal_row = enriched_signal_df.iloc[-2]
                        vol_ma = signal_row.get("volume_ma", 0)
                        volume_ratio = signal_row["volume"] / vol_ma if vol_ma and vol_ma > 0 else 1.0
                        min_quality = max(0.5, min(flex_cfg.get("min_quality_score", 0.7), 1.0))
                        score = compute_signal_quality_score(
                            trade_signal.risk_reward_ratio,
                            trade_signal.rsi,
                            volume_ratio,
                            trade_signal.regime,
                            trade_signal.signal_type,
                            config,
                        )
                        if score >= min_quality:
                            override_applied = True
                            if flex_cfg.get("log_overrides", True):
                                logger.info(
                                    "flexible_cooldown_override",
                                    extra={
                                        "quality_score": round(score, 3),
                                        "min_quality": min_quality,
                                        "elapsed_s": int(elapsed),
                                        "required_s": required,
                                        "reduced_required_s": int(reduced_required),
                                        "side": trade_side_label,
                                    },
                                )
                if not override_applied:
                    logger.info(
                        "trade_skipped_cooldown",
                        extra={
                            "side": trade_side_label,
                            "elapsed_s": int(elapsed),
                            "required_s": required,
                            "reason": lc_reason,
                        },
                    )
                    already_open_side = True  # Reuse flag to skip trade

        # Validate order through risk manager (always runs if not skipped)
        approved = False
        position_size = 0.0
        if not already_open_side:
            approved, reason, position_size = self._risk_mgr.validate_order(
                balance=self._balance,
                entry_price=trade_signal.entry_price,
                stop_loss=trade_signal.stop_loss,
                take_profit=trade_signal.take_profit,
                num_open_positions=num_positions,
                regime=trade_signal.regime,
            )

        if not already_open_side and approved:
            # Check global position limit and duplicate coin gate (multi-bot only)
            if self._portfolio_manager is not None:
                symbol_key = self._config["symbol"]
                pm_allowed = await self._portfolio_manager.can_open(symbol_key)
                if not pm_allowed:
                    logger.info(
                        "trade_skipped_portfolio_limit",
                        extra={
                            "symbol": symbol_key,
                            "global_open": self._portfolio_manager.open_count,
                            "max_global": self._portfolio_manager.max_positions,
                        },
                    )
                    if await self._interruptible_sleep(60):
                        return True
                    return False

            # Validate leverage before placing order
            if not self._risk_mgr.check_leverage(position_size, self._balance):
                logger.warning(
                    "trade_rejected_leverage",
                    extra={
                        "position_size": position_size,
                        "balance": self._balance,
                        "actual_leverage": position_size / self._balance if self._balance > 0 else 0,
                        "max_leverage": config["leverage"],
                    },
                )
                if await self._interruptible_sleep(60):
                    return True
                return False

            # Weekend / low-liquidity filter
            now_utc = datetime.now(tz=timezone.utc)
            if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
                if not config.get("weekend_trading_enabled", True):
                    logger.info(
                        "trade_skipped_weekend",
                        extra={
                            "day": now_utc.strftime("%A"),
                            "reason": "weekend_trading_enabled=false",
                        },
                    )
                    if await self._interruptible_sleep(60):
                        return True
                    return False
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

            # --- AI advisor ---
            ai_result = None
            calibration_id = None
            adjusted_sl = trade_signal.stop_loss
            adjusted_tp = trade_signal.take_profit
            adjusted_size = position_size

            if self._ai_enabled:
                sl_pct = (
                    abs(trade_signal.entry_price - trade_signal.stop_loss)
                    / trade_signal.entry_price * 100
                )
                tp_pct = (
                    abs(trade_signal.take_profit - trade_signal.entry_price)
                    / trade_signal.entry_price * 100
                )
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
                market_ctx = await self._ctx_builder.build(
                    config["symbol"],
                    self._risk_mgr.state,
                    signal_df=enriched_signal_df,
                    trend_df=trend_df,
                )

                # Get AI advisor decision
                ai_result = await self._ai_analyst.analyze(candidate, market_ctx)

                # Record decision for calibration
                if self._calibration_tracker:
                    calibration_id = self._calibration_tracker.record_decision(
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

                # Wire AI regime to risk manager: use more conservative regime
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
                    _, _, position_size = self._risk_mgr.validate_order(
                        balance=self._balance,
                        entry_price=trade_signal.entry_price,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        num_open_positions=num_positions,
                        regime=ai_regime,
                    )

                # Only skip for hard-stop conditions (AI says should_skip)
                if ai_result.decision == "skip":
                    if self._log_all_decisions:
                        self._journal.log_ai_decision(
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
                    if await self._interruptible_sleep(60):
                        return True
                    return False

                # Apply AI adjustments to trade parameters
                influence_mult = (
                    self._calibration_tracker.get_influence_multiplier()
                    if self._calibration_tracker
                    else 1.0
                )

                adjusted_size, adjusted_sl, adjusted_tp = _apply_ai_adjustments(
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    entry_price=trade_signal.entry_price,
                    position_size=position_size,
                    ai_result=ai_result,
                    signal_type=trade_signal.signal_type,
                    influence_multiplier=influence_mult,
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
                if not self._risk_mgr.check_leverage(adjusted_size, self._balance):
                    adjusted_size = self._balance * config["leverage"]
                    logger.warning(
                        "ai_size_capped_by_leverage",
                        extra={"capped_size": adjusted_size},
                    )

            # --- Execute trade ---
            contract_size = adjusted_size / trade_signal.entry_price

            order = self._client.place_order(
                side=side,
                size=round(contract_size, 6),
                sl=self._client.price_precision(adjusted_sl),
                tp=self._client.price_precision(adjusted_tp),
            )

            # Verify SL was actually set with retry
            sl_verified = False
            for sl_attempt in range(3):
                try:
                    await asyncio.sleep(0.3 * (sl_attempt + 1))
                    if self._client._exchange_name == "binance":
                        # Binance: SL is an algo conditional order
                        algo_orders = self._client._get_binance_algo_orders(config["symbol"])
                        for ao in algo_orders:
                            if str(ao.get("orderType", "")).upper() == "STOP_MARKET":
                                sl_verified = True
                                break
                    else:
                        # Bybit: SL embedded in position
                        verify_positions = self._client.get_positions()
                        for vp in verify_positions:
                            if vp.get("side") == ("long" if side == "buy" else "short"):
                                sl_val = float(
                                    vp.get("stopLossPrice")
                                    or vp.get("stopLoss")
                                    or vp.get("info", {}).get("stopPrice")
                                    or vp.get("info", {}).get("stopLoss", 0)
                                    or 0
                                )
                                if sl_val > 0:
                                    sl_verified = True
                                break
                    if sl_verified:
                        break
                    logger.warning(
                        "sl_not_set_retrying",
                        extra={"attempt": sl_attempt + 1},
                    )
                    self._client.modify_sl(
                        symbol=config["symbol"],
                        side=side,
                        new_sl=self._client.price_precision(adjusted_sl),
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
                send_alert(f"⚠️ <b>{config['symbol']}</b> SL verify failed — emergency close!")
                close_ok = False
                try:
                    # Cancel algo orders first to prevent orphaned SL/TP
                    self._client.cancel_all_orders()
                    self._client.close_all_positions()
                    close_ok = True
                except Exception as close_err:
                    logger.critical(
                        "sl_verification_and_close_failed_halting",
                        extra={"order_id": order.order_id, "error": str(close_err)},
                    )
                if not close_ok:
                    # Cannot verify SL AND cannot close position — must halt immediately
                    self._shutdown_event.set()
                    return True
                if await self._interruptible_sleep(60):
                    return True
                return False

            # Verify TP was set (Binance: check algo orders for TAKE_PROFIT_MARKET)
            tp_verified = False
            for tp_attempt in range(3):
                try:
                    await asyncio.sleep(0.3 * (tp_attempt + 1))
                    if self._client._exchange_name == "binance":
                        # Binance: TP is an algo conditional order
                        algo_orders = self._client._get_binance_algo_orders(config["symbol"])
                        for ao in algo_orders:
                            if str(ao.get("orderType", "")).upper() == "TAKE_PROFIT_MARKET":
                                tp_verified = True
                                break
                    else:
                        # Bybit: TP embedded in position — check position field
                        verify_positions = self._client.get_positions()
                        for vp in verify_positions:
                            if vp.get("side") == ("long" if side == "buy" else "short"):
                                tp_val = float(
                                    vp.get("takeProfitPrice")
                                    or vp.get("takeProfit")
                                    or vp.get("info", {}).get("takeProfit", 0)
                                    or 0
                                )
                                if tp_val > 0:
                                    tp_verified = True
                                break
                    if tp_verified:
                        break
                    logger.warning(
                        "tp_not_set_retrying",
                        extra={"attempt": tp_attempt + 1},
                    )
                    # Retry placing TP via ccxt stopLossPrice/takeProfitPrice
                    if self._client._exchange_name == "binance":
                        tp_side = "sell" if side == "buy" else "buy"
                        self._client._retry(
                            self._client.exchange.create_order,
                            config["symbol"], "market", tp_side, contract_size, None,
                            {"takeProfitPrice": self._client.price_precision(adjusted_tp), "reduceOnly": True},
                        )
                except Exception as e:
                    logger.error(
                        "tp_verification_failed",
                        extra={"attempt": tp_attempt + 1, "error": str(e)},
                    )

            if not tp_verified:
                logger.warning(
                    "tp_verification_failed_continuing",
                    extra={"order_id": order.order_id, "tp": adjusted_tp},
                )
                # TP failure is less critical than SL — log warning but continue
                # (trailing stop or manual exit can still protect the trade)

            # Use actual fill price and size, not requested
            actual_entry = order.price if order.price else trade_signal.entry_price
            actual_size = order.size  # Filled size (handles partial fills)

            # Recompute SL/TP from actual fill price, preserving ATR distances
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
                        "old_sl": self._client.price_precision(adjusted_sl),
                        "new_sl": self._client.price_precision(fill_adjusted_sl),
                        "old_tp": self._client.price_precision(adjusted_tp),
                        "new_tp": self._client.price_precision(fill_adjusted_tp),
                    },
                )
                self._client.modify_sl(
                    symbol=config["symbol"],
                    side=side,
                    new_sl=self._client.price_precision(fill_adjusted_sl),
                )
                # Also update TP on exchange (Binance: cancel old + place new)
                self._client.modify_tp_binance(
                    symbol=config["symbol"],
                    side=side,
                    new_tp=self._client.price_precision(fill_adjusted_tp),
                )
                adjusted_sl = fill_adjusted_sl
                adjusted_tp = fill_adjusted_tp

            # Log trade with AI advisor data
            trade_id = self._journal.log_trade_open(
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

            self._tracked_trades[trade_id] = {
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
                # Pre-AI values for value_add calibration
                "original_sl": trade_signal.stop_loss,
                "original_tp": trade_signal.take_profit,
            }

            # Register with portfolio manager (global position tracking)
            if self._portfolio_manager is not None:
                await self._portfolio_manager.register_open(self._config["symbol"])

            self._ctx_builder.record_trade_time()

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
                    "risk_usd": self._balance * config["risk_per_trade"],
                    "ai_decision": ai_result.decision if ai_result else "disabled",
                    "ai_confidence": ai_result.confidence if ai_result else None,
                    "ai_market_regime": ai_result.market_regime if ai_result else None,
                    "calibration_id": calibration_id,
                },
            )
            send_alert(
                f"📈 <b>{config['symbol']}</b> {side.upper()} opened\n"
                f"Entry: {actual_entry}\n"
                f"SL: {adjusted_sl} | TP: {adjusted_tp}\n"
                f"Size: ${adjusted_size:,.0f}"
            )
        elif not already_open_side:
            logger.info("trade_rejected", extra={"reason": reason})

        return False

    # ------------------------------------------------------------------
    # Sleep helpers
    # ------------------------------------------------------------------

    async def _sleep_until_next_candle(self) -> bool:
        """Sleep until ~2s after the next candle close.

        Writes a heartbeat file so deployment monitoring can detect a stalled
        bot.

        Returns:
            True if shutdown was requested during sleep, else False.
        """
        config = self._config
        now = datetime.now(tz=timezone.utc)
        signal_minutes = _candle_seconds(config["timeframe_signal"]) // 60
        minutes_until_close = (signal_minutes - 1) - (now.minute % signal_minutes)
        seconds_until_close = minutes_until_close * 60 + (60 - now.second)

        # Write heartbeat file so deployment monitoring can detect a stalled bot
        # Per-bot heartbeat file so dashboard can show which bots are alive
        symbol_clean = self._config["symbol"].replace("/", "").replace(":", "")
        heartbeat_path = Path(os.getenv("BOT_DATA_DIR", "data")) / f"heartbeat_{symbol_clean}"
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(str(time.time()))

        # Update bot health in SQLite
        try:
            pos_side = None
            pos_size = 0.0
            pos_entry = 0.0
            if self._tracked_trades:
                first_trade = next(iter(self._tracked_trades.values()))
                pos_side = first_trade["side"]
                pos_size = first_trade.get("size", 0)
                pos_entry = first_trade.get("entry_price", 0)

            self._journal.upsert_health(
                symbol=config["symbol"],
                config_file=os.getenv("CONFIG_FILE", "config.json"),
                strategy=config.get("strategy_name", ""),
                mode=self._current_mode.value,
                status="running",
                position_side=pos_side,
                position_size=pos_size,
                position_entry=pos_entry,
                error_count=self._risk_mgr.state.consecutive_api_errors if self._risk_mgr else 0,
                loop_count=self._loop_count,
            )
        except Exception:
            pass  # Health update is non-critical

        # Sleep until ~2s after candle close (give exchange time to finalise)
        sleep_time = max(10, min(seconds_until_close + 2, signal_minutes * 60))
        return await self._interruptible_sleep(sleep_time)

    async def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep for up to ``seconds``, waking early if shutdown is requested.

        Args:
            seconds: Maximum sleep duration.

        Returns:
            True if shutdown was requested (caller should break/return),
            False if the full sleep elapsed normally.
        """
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)
            return True  # Shutdown requested
        except asyncio.TimeoutError:
            return False  # Normal timeout — continue

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    async def _handle_error(self, error: Exception) -> bool:
        """Record error and check whether the bot should halt.

        Args:
            error: Exception caught in the main loop.

        Returns:
            True if the main loop should break (halt triggered), else False.
        """
        self._risk_mgr.record_api_error()
        logger.error("trading_loop_error", extra={"error": str(error)})

        # Check if we should halt due to repeated API errors
        can_trade, reason = self._risk_mgr.can_trade(self._balance, 0)
        if not can_trade and "API error" in reason:
            logger.critical("bot_halted_api_errors", extra={"reason": reason})
            send_alert(
                f"🛑 <b>{self._config['symbol']}</b> HALTED — repeated API errors\n{reason}"
            )
            try:
                self._client.cancel_all_orders()
                self._client.close_all_positions()
            except Exception:
                pass
            return True

        if await self._interruptible_sleep(30):
            return True
        return False

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        """Cancel orders, close positions, flush DB connections."""
        config = self._config
        logger.warning(
            "bot_shutting_down", extra={"tracked_trades": len(self._tracked_trades)}
        )
        try:
            self._client.cancel_all_orders()
            remaining_positions = self._client.get_positions()
            if remaining_positions:
                logger.warning(
                    "closing_positions_on_shutdown",
                    extra={"count": len(remaining_positions)},
                )
                self._client.close_all_positions()
                # Notify portfolio manager of all positions being closed
                if self._portfolio_manager is not None:
                    for _ in self._tracked_trades:
                        await self._portfolio_manager.register_close(self._config["symbol"])
                # Record closures for tracked trades
                for trade_id, info in self._tracked_trades.items():
                    try:
                        current_price = self._client.get_ticker_price(config["symbol"])
                        trade_side = "long" if info["side"] == "buy" else "short"
                        if trade_side == "long":
                            pnl = (
                                (current_price - info["entry_price"])
                                / info["entry_price"]
                                * info["size"]
                            )
                        else:
                            pnl = (
                                (info["entry_price"] - current_price)
                                / info["entry_price"]
                                * info["size"]
                            )
                        pnl_pct = pnl / info["size"] * 100 if info["size"] > 0 else 0
                        duration = int(time.time() - info["open_time"])
                        self._journal.log_trade_close(
                            trade_id=trade_id,
                            exit_price=current_price,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                            close_reason="graceful_shutdown",
                            duration_seconds=duration,
                        )
                        self._risk_mgr.record_trade_result(pnl)
                    except Exception as close_err:
                        logger.error(
                            "shutdown_close_log_failed", extra={"error": str(close_err)}
                        )
        except Exception as e:
            logger.error("graceful_shutdown_failed", extra={"error": str(e)})

        try:
            self._journal.upsert_health(
                symbol=config["symbol"],
                status="stopped",
                mode=self._current_mode.value,
            )
        except Exception:
            pass

        # Note: per-bot stop alert removed to avoid spam.
        # main_multi.py sends a single summary alert instead.
        logger.info("bot_stopped")

        # Close persistent database connections
        try:
            self._journal.close()
        except Exception:
            pass
        if self._calibration_tracker:
            try:
                self._calibration_tracker.close()
            except Exception:
                pass
