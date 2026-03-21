"""Main Streamlit trading dashboard application.

Run with:
    streamlit run dashboard/app.py --server.port 8501
"""

import html as _html
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import streamlit as st

from dashboard.components import (
    COLORS,
    advisor_adjustments_chart,
    ai_confidence_histogram,
    ai_decision_pie,
    calibration_curve_chart,
    candlestick_chart,
    daily_pnl_bar_chart,
    equity_curve_chart,
    per_bot_pnl_bar_chart,
    portfolio_summary_table,
    regime_accuracy_chart,
    risk_gauge,
    rsi_chart,
)
from dashboard.queries import (
    db_exists,
    get_ai_decisions,
    get_bot_statuses,
    get_calibration_data,
    get_calibration_stats,
    get_closed_trades,
    get_consecutive_losses,
    get_daily_pnl,
    get_distinct_symbols,
    get_equity_curve,
    get_open_trades,
    get_per_bot_summary,
    get_recent_logs,
    get_recent_trades,
    get_today_pnl,
    get_trade_stats,
)

LOG_PATH = str(Path(__file__).resolve().parent.parent / "trading_bot.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Crypto Trading Bot",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark theme CSS overrides
st.markdown(
    """
    <style>
    /* Force dark background */
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    /* Metric cards */
    [data-testid="stMetric"] {
        background-color: #1E2530;
        border-radius: 8px;
        padding: 12px 16px;
        border: 1px solid #2D3748;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.4rem;
    }
    /* Status badges */
    .status-running { color: #00C853; font-weight: bold; }
    .status-halted { color: #FF1744; font-weight: bold; }
    .status-on { color: #00C853; }
    .status-off { color: #78909C; }
    /* Dataframe styling */
    .stDataFrame { font-size: 0.85rem; }
    /* Circuit breaker indicators */
    .cb-green { display: inline-block; width: 12px; height: 12px; border-radius: 50%; background: #00C853; margin-right: 6px; }
    .cb-yellow { display: inline-block; width: 12px; height: 12px; border-radius: 50%; background: #FFD600; margin-right: 6px; }
    .cb-red { display: inline-block; width: 12px; height: 12px; border-radius: 50%; background: #FF1744; margin-right: 6px; }
    /* Log viewer */
    .log-container {
        background: #151A22;
        border: 1px solid #2D3748;
        border-radius: 8px;
        padding: 12px;
        max-height: 500px;
        overflow-y: auto;
        font-family: 'Courier New', monospace;
        font-size: 0.8rem;
        line-height: 1.5;
    }
    .log-entry {
        padding: 2px 6px;
        border-bottom: 1px solid #1E2530;
        white-space: pre-wrap;
        word-break: break-all;
    }
    .log-level-INFO { color: #78909C; }
    .log-level-WARNING { color: #FFD600; }
    .log-level-ERROR { color: #FF1744; font-weight: bold; }
    .log-level-CRITICAL { color: #FF1744; font-weight: bold; background: rgba(255,23,68,0.1); }
    .log-level-DEBUG { color: #546E7A; }
    /* Bot status grid — compact pills */
    .bot-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 8px 0 12px 0;
    }
    .bot-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 10px;
        border-radius: 16px;
        font-size: 0.78rem;
        font-weight: 500;
        white-space: nowrap;
    }
    .bot-pill.running {
        background: rgba(0,200,83,0.12);
        border: 1px solid rgba(0,200,83,0.3);
        color: #B9F6CA;
    }
    .bot-pill.stopped {
        background: rgba(255,23,68,0.10);
        border: 1px solid rgba(255,23,68,0.25);
        color: #FF8A80;
    }
    .bot-pill .dot {
        width: 7px; height: 7px;
        border-radius: 50%;
        display: inline-block;
        flex-shrink: 0;
    }
    .bot-pill.running .dot { background: #00C853; }
    .bot-pill.stopped .dot { background: #FF1744; }
    .bot-pill .strat {
        color: #78909C;
        font-size: 0.68rem;
        font-weight: 400;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Config discovery — scan config*.json for symbol→config mapping
# ---------------------------------------------------------------------------
DB_PATH = str(PROJECT_ROOT / "trades.db")


@st.cache_data(ttl=300)
def discover_configs() -> Dict[str, str]:
    """Scan config*.json files, build {symbol: config_path} map."""
    mapping: Dict[str, str] = {}
    for p in sorted(PROJECT_ROOT.glob("config*.json")):
        try:
            with open(p) as f:
                cfg = json.load(f)
            sym = cfg.get("symbol")
            if sym:
                # Prefer more specific configs over generic ones
                if sym not in mapping or len(p.stem) > len(Path(mapping[sym]).stem):
                    mapping[sym] = str(p)
        except Exception:
            continue
    return mapping


@st.cache_data(ttl=30)
def load_config_for_symbol(config_path: str) -> dict:
    """Load a config file by path."""
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Auto-refresh every 30 seconds
# ---------------------------------------------------------------------------
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()

# ---------------------------------------------------------------------------
# Sidebar — Bot Selector + Settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Bot Selector")

    # Discover symbols from DB
    symbols = get_distinct_symbols(db_path=DB_PATH)
    options = ["Portfolio (All Bots)"] + symbols
    selected = st.selectbox("View", options, index=0)

    selected_symbol = None if selected == "Portfolio (All Bots)" else selected
    is_portfolio_view = selected_symbol is None

    st.markdown("---")
    st.markdown("### Settings")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=True)
    if st.button("Refresh Now"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown("### Log Viewer")
    log_lines = st.slider("Log lines", 50, 500, 100, step=50)
    log_level = st.selectbox("Min level", ["ALL", "INFO", "WARNING", "ERROR", "CRITICAL"])
    log_search = st.text_input("Search logs", "")
    st.markdown("---")
    st.markdown(f"**DB Path:** `{DB_PATH}`")
    has_db = db_exists(DB_PATH)
    st.markdown(f"**DB Status:** {'Connected' if has_db else 'Not found'}")
    st.markdown(f"**Symbols:** {len(symbols)} active")

# Auto-refresh logic
if auto_refresh:
    elapsed = time.time() - st.session_state.last_refresh
    if elapsed > 30:
        st.session_state.last_refresh = time.time()
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# Resolve config for current view
# ---------------------------------------------------------------------------
config_map = discover_configs()

if selected_symbol and selected_symbol in config_map:
    config = load_config_for_symbol(config_map[selected_symbol])
else:
    # Portfolio view or unknown symbol — use default config.json
    default_config_path = str(PROJECT_ROOT / "config.json")
    config = load_config_for_symbol(default_config_path)

# ---------------------------------------------------------------------------
# Cached data loaders (symbol-aware)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def load_recent_trades(limit: int = 100, symbol: Optional[str] = None) -> pd.DataFrame:
    return get_recent_trades(limit=limit, db_path=DB_PATH, symbol=symbol)


@st.cache_data(ttl=30)
def load_trade_stats(symbol: Optional[str] = None) -> dict:
    return get_trade_stats(db_path=DB_PATH, symbol=symbol)


@st.cache_data(ttl=30)
def load_equity_curve(symbol: Optional[str] = None) -> pd.DataFrame:
    return get_equity_curve(db_path=DB_PATH, symbol=symbol)


@st.cache_data(ttl=30)
def load_ai_decisions() -> pd.DataFrame:
    return get_ai_decisions(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_daily_pnl_data(symbol: Optional[str] = None) -> pd.DataFrame:
    return get_daily_pnl(db_path=DB_PATH, symbol=symbol)


@st.cache_data(ttl=30)
def load_open_trades(symbol: Optional[str] = None) -> pd.DataFrame:
    return get_open_trades(db_path=DB_PATH, symbol=symbol)


@st.cache_data(ttl=30)
def load_calibration_data() -> pd.DataFrame:
    return get_calibration_data(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_calibration_stats() -> dict:
    return get_calibration_stats(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_recent_logs(max_lines: int = 100, min_level: str = "ALL", search: str = "") -> list:
    return get_recent_logs(max_lines=max_lines, min_level=min_level, search=search, log_path=LOG_PATH)


@st.cache_data(ttl=30)
def load_per_bot_summary() -> pd.DataFrame:
    return get_per_bot_summary(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_bot_statuses() -> list:
    return get_bot_statuses(PROJECT_ROOT)


# ---------------------------------------------------------------------------
# HEADER — Account Overview
# ---------------------------------------------------------------------------
if is_portfolio_view:
    st.markdown("## Portfolio Dashboard")
else:
    st.markdown(f"## {selected_symbol} Dashboard")

today_pnl = get_today_pnl(db_path=DB_PATH, symbol=selected_symbol)
open_trades = load_open_trades(symbol=selected_symbol)
stats = load_trade_stats(symbol=selected_symbol)
consec_losses = get_consecutive_losses(db_path=DB_PATH, symbol=selected_symbol)

mode = "TESTNET" if config.get("use_testnet", True) else "LIVE"
max_daily_loss = config.get("max_daily_loss", 0.02)
max_positions = config.get("max_positions", 2)
max_consec_losses = config.get("max_consecutive_losses", 3)

# Header metrics row
if is_portfolio_view:
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total PnL", f"${stats['total_pnl']:,.2f}")
    with col2:
        pnl_prefix = "+" if today_pnl >= 0 else ""
        st.metric("Today PnL", f"{pnl_prefix}${today_pnl:,.2f}")
    with col3:
        st.metric("Active Bots", f"{len(symbols)}")
    with col4:
        st.metric("Open Positions", f"{len(open_trades)}")
    with col5:
        st.metric("Mode", mode)
else:
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Total PnL", f"${stats['total_pnl']:,.2f}")
    with col2:
        pnl_prefix = "+" if today_pnl >= 0 else ""
        st.metric("Daily PnL", f"{pnl_prefix}${today_pnl:,.2f}")
    with col3:
        st.metric("Open Positions", f"{len(open_trades)}/{max_positions}")
    with col4:
        is_halted = (
            consec_losses >= max_consec_losses
            or (today_pnl < 0 and stats.get("total_pnl", 0) != 0 and abs(today_pnl) > max_daily_loss * 1000)
        )
        st.metric("Bot Status", "HALTED" if is_halted else "RUNNING")
    with col5:
        st.metric("Mode", mode)
    with col6:
        ai_config = config.get("ai_layer", {})
        ai_enabled = ai_config.get("enabled", False)
        ai_threshold = ai_config.get("confidence_threshold", 0.0)
        st.metric("AI Layer", f"ON ({ai_threshold})" if ai_enabled else "OFF")

st.markdown("---")

# ---------------------------------------------------------------------------
# PORTFOLIO VIEW
# ---------------------------------------------------------------------------
if is_portfolio_view:
    # -------------------------------------------------------------------
    # Bot Status — compact pill grid
    # -------------------------------------------------------------------
    bot_statuses = load_bot_statuses()

    if bot_statuses:
        running_count = sum(1 for b in bot_statuses if b["status"] == "running")
        stopped_count = sum(1 for b in bot_statuses if b["status"] == "stopped")
        total_count = len(bot_statuses)

        # Summary in header metrics row
        status_color = "#00C853" if running_count == total_count else (
            "#FFD600" if running_count > 0 else "#FF1744"
        )

        # Collapsible section — expanded by default only if something is stopped
        with st.expander(
            f"Bot Status  |  {running_count} running  {'  /  ' + str(stopped_count) + ' stopped' if stopped_count > 0 else ''}",
            expanded=stopped_count > 0,
        ):
            # Group by strategy
            from collections import defaultdict
            by_strategy: dict[str, list] = defaultdict(list)
            for b in bot_statuses:
                by_strategy[b["strategy"]].append(b)

            # Sort strategies: those with stopped bots first
            def _strat_sort_key(item):
                strat, bots = item
                has_stopped = any(b["status"] == "stopped" for b in bots)
                return (0 if has_stopped else 1, strat)

            pills_html = ""
            for strat, bots in sorted(by_strategy.items(), key=_strat_sort_key):
                strat_pills = ""
                for b in sorted(bots, key=lambda x: x["symbol"]):
                    css_class = "running" if b["status"] == "running" else "stopped"
                    # Short symbol: remove USDT suffix for display
                    short_sym = b["symbol"].replace("USDT", "")
                    strat_pills += (
                        f'<span class="bot-pill {css_class}">'
                        f'<span class="dot"></span>{short_sym}'
                        f'</span>'
                    )
                pills_html += (
                    f'<div style="margin-bottom:6px;">'
                    f'<span style="color:#546E7A; font-size:0.72rem; margin-right:6px;">{strat}</span>'
                    f'<div class="bot-grid">{strat_pills}</div>'
                    f'</div>'
                )

            st.markdown(pills_html, unsafe_allow_html=True)

    st.markdown("---")

    # Per-bot summary table
    st.markdown("### Per-Bot Summary")
    bot_summary = load_per_bot_summary()

    if bot_summary.empty:
        st.info("No trades yet. Trades will appear here once any bot executes its first trade.")
    else:
        display_summary = portfolio_summary_table(bot_summary)
        st.dataframe(display_summary, use_container_width=True, height=min(400, 40 + len(display_summary) * 35))

        # Charts row: Equity curve + Per-bot PnL
        chart_col1, chart_col2 = st.columns([3, 2])

        with chart_col1:
            equity_data = load_equity_curve(symbol=None)
            st.plotly_chart(
                equity_curve_chart(equity_data),
                use_container_width=True,
                key="portfolio_equity",
            )

        with chart_col2:
            st.plotly_chart(
                per_bot_pnl_bar_chart(bot_summary),
                use_container_width=True,
                key="per_bot_pnl",
            )

        # Daily PnL
        daily_pnl_data = load_daily_pnl_data(symbol=None)
        if not daily_pnl_data.empty:
            st.plotly_chart(
                daily_pnl_bar_chart(daily_pnl_data),
                use_container_width=True,
                key="portfolio_daily_pnl",
            )

    # Recent trades (all symbols, showing symbol column)
    st.markdown("### Recent Trades (All Bots)")
    recent_trades = load_recent_trades(100, symbol=None)

    if not recent_trades.empty:
        display_cols = [
            "timestamp", "symbol", "side", "signal_source", "entry_price", "exit_price",
            "pnl", "pnl_pct", "status", "close_reason",
        ]
        available_cols = [c for c in display_cols if c in recent_trades.columns]
        trade_display = recent_trades[available_cols].copy()

        if "pnl" in trade_display.columns:
            trade_display["pnl"] = trade_display["pnl"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )
        if "pnl_pct" in trade_display.columns:
            trade_display["pnl_pct"] = trade_display["pnl_pct"].apply(
                lambda x: f"{x:,.2f}%" if pd.notna(x) else "-"
            )
        if "entry_price" in trade_display.columns:
            trade_display["entry_price"] = trade_display["entry_price"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )
        if "exit_price" in trade_display.columns:
            trade_display["exit_price"] = trade_display["exit_price"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )

        st.dataframe(trade_display, use_container_width=True, height=300)

    st.markdown("---")

# ---------------------------------------------------------------------------
# SINGLE-BOT VIEW
# ---------------------------------------------------------------------------
else:
    # Price Chart (candlestick + RSI) — only for single-bot view
    st.markdown("### Price & Indicators")
    try:
        from bot.exchange import BybitClient

        client = BybitClient(config)
        symbol_raw = config.get("symbol", selected_symbol)
        timeframe = config.get("timeframe_signal", "15m")

        @st.cache_data(ttl=30)
        def load_candles(_symbol: str, _timeframe: str) -> pd.DataFrame:
            from bot.data import add_indicators
            df = client.get_ohlcv(_symbol, _timeframe, limit=100)
            df = add_indicators(df, config)
            return df

        candle_df = load_candles(symbol_raw, timeframe)
        recent_for_chart = load_recent_trades(50, symbol=selected_symbol)

        st.plotly_chart(
            candlestick_chart(candle_df, recent_for_chart),
            use_container_width=True,
            key="candlestick",
        )
        st.plotly_chart(
            rsi_chart(candle_df),
            use_container_width=True,
            key="rsi",
        )
    except Exception as e:
        st.info(
            f"Price chart unavailable (exchange not connected: {type(e).__name__}). "
            "Charts will appear when the bot is running with valid API credentials."
        )

    st.markdown("---")

    # Trade Log & Performance
    st.markdown("### Trade Log & Performance")
    recent_trades = load_recent_trades(100, symbol=selected_symbol)

    if recent_trades.empty:
        st.info("No trades yet for this symbol.")
    else:
        display_cols = [
            "timestamp", "side", "signal_source", "entry_price", "exit_price", "pnl", "pnl_pct",
            "duration_seconds", "ai_decision", "status", "close_reason",
        ]
        available_cols = [c for c in display_cols if c in recent_trades.columns]
        trade_display = recent_trades[available_cols].copy()

        if "pnl" in trade_display.columns:
            trade_display["pnl"] = trade_display["pnl"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )
        if "pnl_pct" in trade_display.columns:
            trade_display["pnl_pct"] = trade_display["pnl_pct"].apply(
                lambda x: f"{x:,.2f}%" if pd.notna(x) else "-"
            )
        if "entry_price" in trade_display.columns:
            trade_display["entry_price"] = trade_display["entry_price"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )
        if "exit_price" in trade_display.columns:
            trade_display["exit_price"] = trade_display["exit_price"].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else "-"
            )
        if "duration_seconds" in trade_display.columns:
            def fmt_duration(secs):
                if pd.isna(secs) or secs is None:
                    return "-"
                secs = int(secs)
                hours, remainder = divmod(secs, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    return f"{hours}h {minutes}m"
                return f"{minutes}m {seconds}s"

            trade_display["duration_seconds"] = trade_display["duration_seconds"].apply(fmt_duration)
            trade_display = trade_display.rename(columns={"duration_seconds": "duration"})

        st.dataframe(trade_display, use_container_width=True, height=300)

        # Performance stats + Equity curve
        perf_col, equity_col = st.columns([1, 2])

        with perf_col:
            st.markdown("#### Performance Stats")
            stats_items = [
                ("Total Trades", str(stats["total_trades"])),
                ("Win Rate", f"{stats['win_rate']:.1%}"),
                ("Wins / Losses", f"{stats['wins']} / {stats['losses']}"),
                ("Profit Factor", f"{stats['profit_factor']:.2f}" if stats["profit_factor"] != float("inf") else "N/A"),
                ("Avg Win", f"${stats['avg_win']:,.2f}"),
                ("Avg Loss", f"${stats['avg_loss']:,.2f}"),
                ("Max Drawdown", f"${stats['max_drawdown']:,.2f} ({stats['max_drawdown_pct']:.1%})"),
                ("Sharpe Ratio", f"{stats['sharpe_ratio']:.2f}"),
                ("Total PnL", f"${stats['total_pnl']:,.2f}"),
            ]
            for label, value in stats_items:
                st.markdown(f"**{label}:** {value}")

        with equity_col:
            equity_data = load_equity_curve(symbol=selected_symbol)
            st.plotly_chart(
                equity_curve_chart(equity_data),
                use_container_width=True,
                key="equity_curve",
            )

        # Daily PnL
        daily_pnl_data = load_daily_pnl_data(symbol=selected_symbol)
        if not daily_pnl_data.empty:
            st.plotly_chart(
                daily_pnl_bar_chart(daily_pnl_data),
                use_container_width=True,
                key="daily_pnl",
            )

    st.markdown("---")

    # Risk Monitor
    st.markdown("### Risk Monitor")
    risk_col1, risk_col2, risk_col3 = st.columns(3)

    with risk_col1:
        daily_loss_pct = 0.0
        if stats["total_pnl"] != 0:
            est_balance = max(abs(stats["total_pnl"]) * 50, 1000)
            daily_loss_pct = abs(min(today_pnl, 0)) / est_balance if est_balance > 0 else 0.0

        st.plotly_chart(
            risk_gauge(daily_loss_pct, max_daily_loss, title="Daily Loss"),
            use_container_width=True,
            key="daily_loss_gauge",
        )

    with risk_col2:
        max_leverage = config.get("leverage", 5)
        st.markdown("#### Leverage")
        st.markdown(f"**Max Configured:** {max_leverage}x")
        st.markdown(f"**Risk Per Trade:** {config.get('risk_per_trade', 0.005) * 100:.1f}%")
        st.markdown(f"**Min R:R Ratio:** {config.get('min_rr_ratio', 2.0)}")

        st.markdown("#### Consecutive Losses")
        loss_ratio = consec_losses / max_consec_losses if max_consec_losses > 0 else 0
        st.progress(min(loss_ratio, 1.0), text=f"{consec_losses} / {max_consec_losses}")

    with risk_col3:
        st.markdown("#### Circuit Breaker Status")
        daily_loss_status = "green" if daily_loss_pct < max_daily_loss * 0.5 else (
            "yellow" if daily_loss_pct < max_daily_loss * 0.8 else "red"
        )
        st.markdown(
            f'<span class="cb-{daily_loss_status}"></span> Daily Loss Limit '
            f'({daily_loss_pct * 100:.2f}% / {max_daily_loss * 100:.0f}%)',
            unsafe_allow_html=True,
        )
        consec_status = "green" if consec_losses < max_consec_losses * 0.5 else (
            "yellow" if consec_losses < max_consec_losses else "red"
        )
        st.markdown(
            f'<span class="cb-{consec_status}"></span> Consecutive Losses '
            f'({consec_losses} / {max_consec_losses})',
            unsafe_allow_html=True,
        )
        api_max = config.get("max_api_errors", 3)
        st.markdown(
            f'<span class="cb-green"></span> API Errors (limit: {api_max})',
            unsafe_allow_html=True,
        )
        cooldown_h = config.get("cooldown_hours", 2)
        st.markdown(
            f'<span class="cb-green"></span> Cooldown Period ({cooldown_h}h after {max_consec_losses} losses)',
            unsafe_allow_html=True,
        )

    st.markdown("---")

# ---------------------------------------------------------------------------
# AI Analytics (both views)
# ---------------------------------------------------------------------------
ai_config = config.get("ai_layer", {})
ai_enabled = ai_config.get("enabled", False)

if ai_enabled:
    st.markdown("### AI Advisor Analytics")

    ai_data = load_ai_decisions()
    cal_data = load_calibration_data()
    cal_stats = load_calibration_stats()

    if ai_data.empty and cal_data.empty:
        st.info("No AI decisions recorded yet. Data will appear as the AI advisor makes decisions.")
    else:
        cal_col1, cal_col2, cal_col3, cal_col4, cal_col5 = st.columns(5)
        with cal_col1:
            st.metric("Total Decisions", cal_stats["total_decisions"])
        with cal_col2:
            st.metric("Decided Trades", cal_stats["decided_trades"])
        with cal_col3:
            acc_str = f"{cal_stats['accuracy']:.1%}" if cal_stats["decided_trades"] > 0 else "N/A"
            st.metric("Win Rate", acc_str)
        with cal_col4:
            avg_conf = f"{cal_stats['avg_stated_confidence']:.0%}" if cal_stats["decided_trades"] > 0 else "N/A"
            st.metric("Avg Confidence", avg_conf)
        with cal_col5:
            st.metric("Influence Mult", f"{cal_stats['influence_multiplier']:.2f}x")

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            if not cal_data.empty:
                st.plotly_chart(calibration_curve_chart(cal_data), use_container_width=True, key="cal_curve")
            else:
                st.plotly_chart(ai_confidence_histogram(ai_data), use_container_width=True, key="ai_hist")

        with chart_col2:
            if not cal_data.empty:
                st.plotly_chart(regime_accuracy_chart(cal_data), use_container_width=True, key="regime_acc")
            else:
                st.plotly_chart(ai_decision_pie(ai_data), use_container_width=True, key="ai_pie")

        adj_col1, adj_col2 = st.columns(2)
        with adj_col1:
            if not cal_data.empty:
                st.plotly_chart(advisor_adjustments_chart(cal_data), use_container_width=True, key="adj_chart")
        with adj_col2:
            if not ai_data.empty:
                st.plotly_chart(ai_decision_pie(ai_data), use_container_width=True, key="ai_pie")

        if not ai_data.empty:
            with st.expander("AI Decision Details"):
                ai_display_cols = ["timestamp", "side", "entry_price", "ai_decision", "ai_confidence", "pnl", "status"]
                available_ai_cols = [c for c in ai_display_cols if c in ai_data.columns]
                st.dataframe(ai_data[available_ai_cols], use_container_width=True, height=250)

        if not cal_data.empty:
            with st.expander("Calibration Tracker Details"):
                cal_display_cols = [
                    "timestamp", "side", "entry_price", "stated_confidence",
                    "position_size_modifier", "sl_adjustment", "tp_adjustment",
                    "market_regime", "outcome", "pnl", "was_correct",
                ]
                available_cal_cols = [c for c in cal_display_cols if c in cal_data.columns]
                st.dataframe(cal_data[available_cal_cols], use_container_width=True, height=250)

st.markdown("---")

# ---------------------------------------------------------------------------
# Live Log Viewer
# ---------------------------------------------------------------------------
st.markdown("### Live Log Viewer")

log_entries = load_recent_logs(max_lines=log_lines, min_level=log_level, search=log_search)

if not log_entries:
    if not Path(LOG_PATH).exists():
        st.info("No log file found. Logs will appear here once the bot starts running.")
    else:
        st.info("No log entries match the current filters.")
else:
    level_counts: Dict[str, int] = {}
    for entry in log_entries:
        lvl = entry["level"]
        level_counts[lvl] = level_counts.get(lvl, 0) + 1

    lm_col1, lm_col2, lm_col3, lm_col4, lm_col5 = st.columns(5)
    with lm_col1:
        st.metric("Total Entries", len(log_entries))
    with lm_col2:
        st.metric("INFO", level_counts.get("INFO", 0))
    with lm_col3:
        st.metric("WARNING", level_counts.get("WARNING", 0))
    with lm_col4:
        st.metric("ERROR", level_counts.get("ERROR", 0))
    with lm_col5:
        latest_ts = log_entries[-1]["timestamp"]
        if "T" in latest_ts:
            latest_display = latest_ts.split("T")[1][:8]
        elif " " in latest_ts:
            latest_display = latest_ts.split(" ")[1][:8]
        else:
            latest_display = latest_ts[-8:]
        st.metric("Latest", latest_display)

    html_lines = []
    for entry in log_entries:
        ts = entry["timestamp"]
        if "T" in ts:
            time_str = ts.split("T")[1][:8]
        elif " " in ts:
            time_str = ts.split(" ")[1][:8]
        else:
            time_str = ts[-8:]

        level = entry["level"]
        message = entry["message"]

        data = entry.get("data", {})
        if data and isinstance(data, dict):
            kv_pairs = " ".join(f"{k}={v}" for k, v in data.items())
        else:
            kv_pairs = ""

        message = _html.escape(str(message))
        kv_pairs = _html.escape(str(kv_pairs))

        line = (
            f'<div class="log-entry log-level-{level}">'
            f'{time_str} [{level}] {message}'
        )
        if kv_pairs:
            line += f' <span style="color:#546E7A">{kv_pairs}</span>'
        line += '</div>'
        html_lines.append(line)

    log_html = (
        '<div class="log-container" id="log-container">'
        + "\n".join(html_lines)
        + '</div>'
        + ''  # No auto-scroll needed — newest entries are already at top
    )
    st.markdown(log_html, unsafe_allow_html=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
footer_symbol = selected_symbol or f"{len(symbols)} bots"
st.markdown(
    f"""
    <div style="text-align: center; color: #78909C; font-size: 0.8rem; padding: 10px;">
        Last refreshed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        &nbsp;|&nbsp; View: {footer_symbol}
    </div>
    """,
    unsafe_allow_html=True,
)
