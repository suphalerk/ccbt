"""Reusable Plotly chart components for the trading dashboard."""

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Consistent color scheme
COLORS = {
    "profit": "#00C853",       # Green
    "loss": "#FF1744",         # Red
    "long": "#00C853",         # Green
    "short": "#FF1744",        # Red
    "neutral": "#78909C",      # Grey
    "ema_fast": "#FFD600",     # Yellow
    "ema_slow": "#2979FF",     # Blue
    "rsi_line": "#AB47BC",     # Purple
    "rsi_zone": "rgba(171, 71, 188, 0.1)",
    "bg": "#0E1117",           # Dark background
    "grid": "#1E2530",         # Grid lines
    "text": "#FAFAFA",         # Text
    "candle_up": "#00C853",
    "candle_down": "#FF1744",
    "equity_line": "#00E5FF",  # Cyan
    "gauge_bg": "#1E2530",
    "gauge_safe": "#00C853",
    "gauge_warn": "#FFD600",
    "gauge_danger": "#FF1744",
}

DARK_LAYOUT = dict(
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], size=12),
    xaxis=dict(gridcolor=COLORS["grid"], showgrid=True),
    yaxis=dict(gridcolor=COLORS["grid"], showgrid=True),
    margin=dict(l=50, r=20, t=40, b=30),
    legend=dict(
        bgcolor="rgba(14, 17, 23, 0.8)",
        bordercolor=COLORS["grid"],
        borderwidth=1,
    ),
)


def candlestick_chart(
    df: pd.DataFrame,
    trades: Optional[pd.DataFrame] = None,
    ema_fast_col: str = "ema_fast",
    ema_slow_col: str = "ema_slow",
    height: int = 500,
) -> go.Figure:
    """Create an interactive candlestick chart with EMA overlays and trade markers.

    Args:
        df: OHLCV DataFrame with DatetimeIndex and columns: open, high, low, close.
            Optionally includes ema_fast, ema_slow columns.
        trades: DataFrame with trade records containing: timestamp, side, entry_price,
                exit_price, status.
        ema_fast_col: Column name for the fast EMA.
        ema_slow_col: Column name for the slow EMA.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with candlestick chart.
    """
    fig = go.Figure()

    # Candlesticks
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            increasing=dict(line=dict(color=COLORS["candle_up"]), fillcolor=COLORS["candle_up"]),
            decreasing=dict(line=dict(color=COLORS["candle_down"]), fillcolor=COLORS["candle_down"]),
            name="Price",
            showlegend=False,
        )
    )

    # EMA fast overlay
    if ema_fast_col in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df[ema_fast_col],
                mode="lines",
                name="EMA 9",
                line=dict(color=COLORS["ema_fast"], width=1.5),
            )
        )

    # EMA slow overlay
    if ema_slow_col in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df[ema_slow_col],
                mode="lines",
                name="EMA 21",
                line=dict(color=COLORS["ema_slow"], width=1.5),
            )
        )

    # Trade markers
    if trades is not None and not trades.empty:
        # Entry markers
        for _, trade in trades.iterrows():
            side = str(trade.get("side", "")).lower()
            entry_time = trade.get("timestamp")
            entry_price = trade.get("entry_price")

            if pd.isna(entry_price) or entry_time is None:
                continue

            # Long = green triangle up, Short = red triangle down
            if side in ("buy", "long"):
                marker_symbol = "triangle-up"
                marker_color = COLORS["long"]
                label = "Long Entry"
            else:
                marker_symbol = "triangle-down"
                marker_color = COLORS["short"]
                label = "Short Entry"

            fig.add_trace(
                go.Scatter(
                    x=[entry_time],
                    y=[entry_price],
                    mode="markers",
                    marker=dict(symbol=marker_symbol, size=14, color=marker_color, line=dict(width=1, color="white")),
                    name=label,
                    showlegend=False,
                    hovertext=f"{label}: ${entry_price:,.2f}",
                )
            )

            # Exit markers (x symbol)
            exit_price = trade.get("exit_price")
            if pd.notna(exit_price) and trade.get("status") == "closed":
                pnl = trade.get("pnl", 0)
                exit_color = COLORS["profit"] if (pnl is not None and pnl > 0) else COLORS["loss"]
                fig.add_trace(
                    go.Scatter(
                        x=[entry_time],  # approximate; ideally use exit_time
                        y=[exit_price],
                        mode="markers",
                        marker=dict(symbol="x", size=12, color=exit_color, line=dict(width=2, color=exit_color)),
                        name="Exit",
                        showlegend=False,
                        hovertext=f"Exit: ${exit_price:,.2f} (PnL: ${pnl:,.2f})" if pnl is not None else f"Exit: ${exit_price:,.2f}",
                    )
                )

    fig.update_layout(
        **DARK_LAYOUT,
        height=height,
        title=dict(text="Price Chart", font=dict(size=16)),
        xaxis_rangeslider_visible=False,
        yaxis_title="Price (USDT)",
    )

    return fig


def rsi_chart(df: pd.DataFrame, rsi_col: str = "rsi", height: int = 200) -> go.Figure:
    """Create an RSI subplot chart with overbought/oversold zones.

    Args:
        df: DataFrame with DatetimeIndex and RSI column.
        rsi_col: Column name for RSI values.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with RSI chart.
    """
    fig = go.Figure()

    if rsi_col not in df.columns:
        fig.update_layout(**DARK_LAYOUT, height=height, title="RSI (no data)")
        return fig

    # RSI zone fill (45-65)
    fig.add_hrect(
        y0=45, y1=65,
        fillcolor=COLORS["rsi_zone"],
        line_width=0,
        annotation_text="Zone",
        annotation_position="top left",
    )

    # RSI line
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df[rsi_col],
            mode="lines",
            name="RSI(14)",
            line=dict(color=COLORS["rsi_line"], width=2),
        )
    )

    # Reference lines
    for level, color, dash in [(30, COLORS["loss"], "dash"), (70, COLORS["profit"], "dash")]:
        fig.add_hline(y=level, line=dict(color=color, width=1, dash=dash), opacity=0.5)

    fig.update_layout(
        **DARK_LAYOUT,
        height=height,
        title=dict(text="RSI (14)", font=dict(size=14)),
        yaxis=dict(range=[0, 100], gridcolor=COLORS["grid"]),
        showlegend=False,
    )

    return fig


def equity_curve_chart(equity_df: pd.DataFrame, height: int = 350) -> go.Figure:
    """Create a cumulative PnL equity curve chart.

    Args:
        equity_df: DataFrame with columns: timestamp, cumulative_pnl.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with equity curve.
    """
    fig = go.Figure()

    if equity_df.empty or "cumulative_pnl" not in equity_df.columns:
        fig.update_layout(**DARK_LAYOUT, height=height, title="Equity Curve (no data)")
        return fig

    cumulative = equity_df["cumulative_pnl"].values

    # Color segments green when above 0, red when below
    fig.add_trace(
        go.Scatter(
            x=equity_df["timestamp"],
            y=cumulative,
            mode="lines",
            name="Cumulative PnL",
            line=dict(color=COLORS["equity_line"], width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 229, 255, 0.1)",
        )
    )

    # Zero line
    fig.add_hline(y=0, line=dict(color=COLORS["neutral"], width=1, dash="dash"), opacity=0.5)

    fig.update_layout(
        **DARK_LAYOUT,
        height=height,
        title=dict(text="Equity Curve", font=dict(size=14)),
        yaxis_title="Cumulative PnL ($)",
        xaxis_title="Time",
    )

    return fig


def risk_gauge(current: float, limit: float, title: str = "Daily Loss", height: int = 200) -> go.Figure:
    """Create a gauge chart for risk monitoring.

    Args:
        current: Current value (e.g., current daily loss as decimal).
        limit: Maximum limit (e.g., 0.02 for 2%).
        title: Gauge title.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with gauge chart.
    """
    # Normalize current as percentage of limit
    pct_of_limit = abs(current) / limit * 100 if limit > 0 else 0
    pct_of_limit = min(pct_of_limit, 100)

    if pct_of_limit < 50:
        bar_color = COLORS["gauge_safe"]
    elif pct_of_limit < 80:
        bar_color = COLORS["gauge_warn"]
    else:
        bar_color = COLORS["gauge_danger"]

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=abs(current) * 100,  # show as percentage
            number=dict(suffix="%", font=dict(size=24, color=COLORS["text"])),
            title=dict(text=title, font=dict(size=14, color=COLORS["text"])),
            gauge=dict(
                axis=dict(range=[0, limit * 100], tickcolor=COLORS["text"]),
                bar=dict(color=bar_color),
                bgcolor=COLORS["gauge_bg"],
                borderwidth=1,
                bordercolor=COLORS["grid"],
                steps=[
                    dict(range=[0, limit * 50], color="rgba(0, 200, 83, 0.15)"),
                    dict(range=[limit * 50, limit * 80], color="rgba(255, 214, 0, 0.15)"),
                    dict(range=[limit * 80, limit * 100], color="rgba(255, 23, 68, 0.15)"),
                ],
                threshold=dict(
                    line=dict(color=COLORS["gauge_danger"], width=3),
                    thickness=0.8,
                    value=limit * 100,
                ),
            ),
        )
    )

    fig.update_layout(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"]),
        height=height,
        margin=dict(l=30, r=30, t=50, b=20),
    )

    return fig


def ai_decision_pie(decisions: pd.DataFrame, height: int = 300) -> go.Figure:
    """Create a pie chart of AI decision distribution.

    Args:
        decisions: DataFrame with an 'ai_decision' column.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with pie chart.
    """
    fig = go.Figure()

    if decisions.empty or "ai_decision" not in decisions.columns:
        fig.update_layout(**DARK_LAYOUT, height=height, title="AI Decisions (no data)")
        return fig

    counts = decisions["ai_decision"].value_counts()

    color_map = {
        "execute": COLORS["profit"],
        "skip": COLORS["loss"],
        "wait": COLORS["ema_fast"],
    }
    colors = [color_map.get(label.lower(), COLORS["neutral"]) for label in counts.index]

    fig.add_trace(
        go.Pie(
            labels=counts.index,
            values=counts.values,
            marker=dict(colors=colors, line=dict(color=COLORS["bg"], width=2)),
            textfont=dict(color=COLORS["text"]),
            hole=0.4,
        )
    )

    fig.update_layout(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"]),
        height=height,
        title=dict(text="AI Decision Distribution", font=dict(size=14, color=COLORS["text"])),
        legend=dict(bgcolor="rgba(14, 17, 23, 0.8)", font=dict(color=COLORS["text"])),
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


def ai_confidence_histogram(decisions: pd.DataFrame, height: int = 300) -> go.Figure:
    """Create a histogram of AI confidence scores.

    Args:
        decisions: DataFrame with an 'ai_confidence' column.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with histogram.
    """
    fig = go.Figure()

    if decisions.empty or "ai_confidence" not in decisions.columns:
        fig.update_layout(**DARK_LAYOUT, height=height, title="AI Confidence (no data)")
        return fig

    confidences = decisions["ai_confidence"].dropna()
    if confidences.empty:
        fig.update_layout(**DARK_LAYOUT, height=height, title="AI Confidence (no data)")
        return fig

    fig.add_trace(
        go.Histogram(
            x=confidences,
            nbinsx=20,
            marker=dict(color=COLORS["ema_slow"], line=dict(color=COLORS["bg"], width=1)),
            name="Confidence",
        )
    )

    fig.update_layout(
        **DARK_LAYOUT,
        height=height,
        title=dict(text="AI Confidence Distribution", font=dict(size=14)),
        xaxis_title="Confidence",
        yaxis_title="Count",
        xaxis=dict(range=[0, 1], gridcolor=COLORS["grid"]),
        showlegend=False,
    )

    return fig


def daily_pnl_bar_chart(daily_df: pd.DataFrame, height: int = 300) -> go.Figure:
    """Create a daily PnL bar chart.

    Args:
        daily_df: DataFrame with columns: date, daily_pnl.
        height: Chart height in pixels.

    Returns:
        Plotly Figure with bar chart.
    """
    fig = go.Figure()

    if daily_df.empty or "daily_pnl" not in daily_df.columns:
        fig.update_layout(**DARK_LAYOUT, height=height, title="Daily PnL (no data)")
        return fig

    colors = [
        COLORS["profit"] if v >= 0 else COLORS["loss"]
        for v in daily_df["daily_pnl"]
    ]

    fig.add_trace(
        go.Bar(
            x=daily_df["date"],
            y=daily_df["daily_pnl"],
            marker=dict(color=colors, line=dict(width=0)),
            name="Daily PnL",
        )
    )

    fig.add_hline(y=0, line=dict(color=COLORS["neutral"], width=1, dash="dash"), opacity=0.5)

    fig.update_layout(
        **DARK_LAYOUT,
        height=height,
        title=dict(text="Daily PnL", font=dict(size=14)),
        yaxis_title="PnL ($)",
        xaxis_title="Date",
        showlegend=False,
    )

    return fig
