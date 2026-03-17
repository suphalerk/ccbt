"""Main Streamlit trading dashboard application.

Run with:
    streamlit run dashboard/app.py --server.port 8501
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components import (
    COLORS,
    ai_confidence_histogram,
    ai_decision_pie,
    candlestick_chart,
    daily_pnl_bar_chart,
    equity_curve_chart,
    risk_gauge,
    rsi_chart,
)
from dashboard.queries import (
    db_exists,
    get_ai_decisions,
    get_closed_trades,
    get_consecutive_losses,
    get_daily_pnl,
    get_equity_curve,
    get_open_trades,
    get_recent_trades,
    get_today_pnl,
    get_trade_stats,
)

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Crypto Trading Bot",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="collapsed",
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
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
DB_PATH = str(Path(__file__).resolve().parent.parent / "trades.db")


@st.cache_data(ttl=30)
def load_config() -> dict:
    """Load the bot configuration file."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


config = load_config()

# ---------------------------------------------------------------------------
# Auto-refresh every 30 seconds
# ---------------------------------------------------------------------------
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()

# Show a refresh countdown in the sidebar
with st.sidebar:
    st.markdown("### Settings")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=True)
    if st.button("Refresh Now"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown(f"**DB Path:** `{DB_PATH}`")
    st.markdown(f"**Config:** `{CONFIG_PATH}`")
    has_db = db_exists(DB_PATH)
    st.markdown(f"**DB Status:** {'Connected' if has_db else 'Not found'}")

# Auto-refresh logic
if auto_refresh:
    elapsed = time.time() - st.session_state.last_refresh
    if elapsed > 30:
        st.session_state.last_refresh = time.time()
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def load_recent_trades(limit: int = 100) -> pd.DataFrame:
    return get_recent_trades(limit=limit, db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_trade_stats() -> dict:
    return get_trade_stats(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_equity_curve() -> pd.DataFrame:
    return get_equity_curve(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_ai_decisions() -> pd.DataFrame:
    return get_ai_decisions(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_daily_pnl_data() -> pd.DataFrame:
    return get_daily_pnl(db_path=DB_PATH)


@st.cache_data(ttl=30)
def load_open_trades() -> pd.DataFrame:
    return get_open_trades(db_path=DB_PATH)


# ---------------------------------------------------------------------------
# SECTION 1: Header -- Account Overview
# ---------------------------------------------------------------------------
st.markdown("## Crypto Trading Dashboard")

today_pnl = get_today_pnl(db_path=DB_PATH)
open_trades = load_open_trades()
stats = load_trade_stats()
consec_losses = get_consecutive_losses(db_path=DB_PATH)

# Determine bot status from config and data
mode = "TESTNET" if config.get("use_testnet", True) else "LIVE"
ai_config = config.get("ai_layer", {})
ai_enabled = ai_config.get("enabled", False)
ai_threshold = ai_config.get("confidence_threshold", 0.0)
max_daily_loss = config.get("max_daily_loss", 0.02)
max_leverage = config.get("leverage", 5)
max_positions = config.get("max_positions", 2)
max_consec_losses = config.get("max_consecutive_losses", 3)

# Header metrics row
col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric("Total PnL", f"${stats['total_pnl']:,.2f}")

with col2:
    pnl_prefix = "+" if today_pnl >= 0 else ""
    st.metric("Daily PnL", f"{pnl_prefix}${today_pnl:,.2f}")

with col3:
    st.metric("Open Positions", f"{len(open_trades)}/{max_positions}")

with col4:
    # Determine status based on circuit breaker conditions
    is_halted = (
        consec_losses >= max_consec_losses
        or (today_pnl < 0 and stats.get("total_pnl", 0) != 0 and abs(today_pnl) > max_daily_loss * 1000)
    )
    status_text = "HALTED" if is_halted else "RUNNING"
    st.metric("Bot Status", status_text)

with col5:
    st.metric("Mode", mode)

with col6:
    ai_status = f"ON ({ai_threshold})" if ai_enabled else "OFF"
    st.metric("AI Layer", ai_status)

st.markdown("---")

# ---------------------------------------------------------------------------
# SECTION 2: Price Chart (candlestick + RSI)
# ---------------------------------------------------------------------------
st.markdown("### Price & Indicators")

# Try to load candle data from exchange -- fallback to a placeholder message
# The dashboard reads from the DB for trade data; candle data comes from exchange.
# For offline/demo usage, we show a note.
try:
    from bot.exchange import BybitClient

    client = BybitClient(config)
    symbol = config.get("symbol", "BTCUSDT")
    timeframe = config.get("timeframe_signal", "15m")

    @st.cache_data(ttl=30)
    def load_candles(_symbol: str, _timeframe: str) -> pd.DataFrame:
        from bot.data import add_indicators
        df = client.get_ohlcv(_symbol, _timeframe, limit=100)
        df = add_indicators(df, config)
        return df

    candle_df = load_candles(symbol, timeframe)

    # Get recent trades to overlay on chart
    recent_trades = load_recent_trades(50)

    chart_col, rsi_col_chart = st.columns([1, 1])

    with st.container():
        st.plotly_chart(
            candlestick_chart(candle_df, recent_trades),
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

# ---------------------------------------------------------------------------
# SECTION 3: Trade Log & Performance
# ---------------------------------------------------------------------------
st.markdown("### Trade Log & Performance")

recent_trades = load_recent_trades(100)

if recent_trades.empty:
    st.info("No trades yet. Trades will appear here once the bot executes its first trade.")
else:
    # Trade log table
    display_cols = [
        "timestamp", "side", "entry_price", "exit_price", "pnl", "pnl_pct",
        "duration_seconds", "ai_decision", "status", "close_reason",
    ]
    available_cols = [c for c in display_cols if c in recent_trades.columns]
    trade_display = recent_trades[available_cols].copy()

    # Format columns for readability
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

    # Performance stats + Equity curve side by side
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
        equity_data = load_equity_curve()
        st.plotly_chart(
            equity_curve_chart(equity_data),
            use_container_width=True,
            key="equity_curve",
        )

    # Daily PnL bar chart
    daily_pnl_data = load_daily_pnl_data()
    if not daily_pnl_data.empty:
        st.plotly_chart(
            daily_pnl_bar_chart(daily_pnl_data),
            use_container_width=True,
            key="daily_pnl",
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# SECTION 4: Risk Monitor
# ---------------------------------------------------------------------------
st.markdown("### Risk Monitor")

risk_col1, risk_col2, risk_col3 = st.columns(3)

with risk_col1:
    # Daily loss progress -- estimate from today's PnL relative to a rough balance
    # We use total_pnl as a rough indicator; ideal would be live balance
    daily_loss_pct = 0.0
    if stats["total_pnl"] != 0:
        # Rough estimation: assume starting balance was enough that today_pnl / balance gives a small %
        # In production this would use live balance from exchange
        est_balance = max(abs(stats["total_pnl"]) * 50, 1000)  # rough fallback
        daily_loss_pct = abs(min(today_pnl, 0)) / est_balance if est_balance > 0 else 0.0

    st.plotly_chart(
        risk_gauge(daily_loss_pct, max_daily_loss, title="Daily Loss"),
        use_container_width=True,
        key="daily_loss_gauge",
    )

with risk_col2:
    # Leverage indicator -- show max configured leverage
    st.markdown("#### Leverage")
    st.markdown(f"**Max Configured:** {max_leverage}x")
    st.markdown(f"**Risk Per Trade:** {config.get('risk_per_trade', 0.005) * 100:.1f}%")
    st.markdown(f"**Min R:R Ratio:** {config.get('min_rr_ratio', 2.0)}")

    st.markdown("#### Consecutive Losses")
    loss_ratio = consec_losses / max_consec_losses if max_consec_losses > 0 else 0
    st.progress(min(loss_ratio, 1.0), text=f"{consec_losses} / {max_consec_losses}")

with risk_col3:
    st.markdown("#### Circuit Breaker Status")

    # Daily loss circuit breaker
    daily_loss_status = "green" if daily_loss_pct < max_daily_loss * 0.5 else (
        "yellow" if daily_loss_pct < max_daily_loss * 0.8 else "red"
    )
    st.markdown(
        f'<span class="cb-{daily_loss_status}"></span> Daily Loss Limit '
        f'({daily_loss_pct * 100:.2f}% / {max_daily_loss * 100:.0f}%)',
        unsafe_allow_html=True,
    )

    # Consecutive losses circuit breaker
    consec_status = "green" if consec_losses < max_consec_losses * 0.5 else (
        "yellow" if consec_losses < max_consec_losses else "red"
    )
    st.markdown(
        f'<span class="cb-{consec_status}"></span> Consecutive Losses '
        f'({consec_losses} / {max_consec_losses})',
        unsafe_allow_html=True,
    )

    # API errors -- we cannot read live state, so show config
    api_max = config.get("max_api_errors", 3)
    st.markdown(
        f'<span class="cb-green"></span> API Errors (limit: {api_max})',
        unsafe_allow_html=True,
    )

    # Cooldown
    cooldown_h = config.get("cooldown_hours", 2)
    st.markdown(
        f'<span class="cb-green"></span> Cooldown Period ({cooldown_h}h after {max_consec_losses} losses)',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# SECTION 5: AI Analytics (if enabled)
# ---------------------------------------------------------------------------
if ai_enabled:
    st.markdown("### AI Analytics")

    ai_data = load_ai_decisions()

    if ai_data.empty:
        st.info("No AI decisions recorded yet. Data will appear as the AI layer makes decisions.")
    else:
        ai_col1, ai_col2 = st.columns(2)

        with ai_col1:
            st.plotly_chart(
                ai_decision_pie(ai_data),
                use_container_width=True,
                key="ai_pie",
            )

        with ai_col2:
            st.plotly_chart(
                ai_confidence_histogram(ai_data),
                use_container_width=True,
                key="ai_hist",
            )

        # AI Accuracy Analysis
        st.markdown("#### AI Accuracy")

        executed = ai_data[ai_data["ai_decision"] == "execute"]
        skipped = ai_data[ai_data["ai_decision"] == "skip"]

        acc_col1, acc_col2, acc_col3, acc_col4 = st.columns(4)

        with acc_col1:
            total_executed = len(executed)
            st.metric("Total Executed", total_executed)

        with acc_col2:
            total_skipped = len(skipped)
            st.metric("Total Skipped", total_skipped)

        with acc_col3:
            # Executed trades that were profitable (AI was right to execute)
            if not executed.empty and "pnl" in executed.columns:
                exec_profitable = len(executed[executed["pnl"].fillna(0) > 0])
                exec_accuracy = exec_profitable / total_executed * 100 if total_executed > 0 else 0
                st.metric("Execute Accuracy", f"{exec_accuracy:.1f}%")
            else:
                st.metric("Execute Accuracy", "N/A")

        with acc_col4:
            # Skipped trades: check if entry_price moved favorably (would have won)
            # For skipped trades, we don't have exit data, so count them
            if not skipped.empty:
                override_count = len(skipped[skipped["ai_override"] == 1])
                st.metric("AI Overrides", override_count)
            else:
                st.metric("AI Overrides", 0)

        # Detailed AI decision table
        with st.expander("AI Decision Details"):
            ai_display_cols = [
                "timestamp", "side", "entry_price", "ai_decision",
                "ai_confidence", "pnl", "status",
            ]
            available_ai_cols = [c for c in ai_display_cols if c in ai_data.columns]
            st.dataframe(ai_data[available_ai_cols], use_container_width=True, height=250)

st.markdown("---")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div style="text-align: center; color: #78909C; font-size: 0.8rem; padding: 10px;">
        Last refreshed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        &nbsp;|&nbsp; Symbol: {config.get('symbol', 'N/A')}
        &nbsp;|&nbsp; Timeframe: {config.get('timeframe_signal', 'N/A')}
    </div>
    """,
    unsafe_allow_html=True,
)
