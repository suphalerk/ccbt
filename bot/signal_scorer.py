"""Multi-signal weighted scoring engine for trade conviction.

Supplements the existing binary signal checks in strategy.py.
When enabled, a signal must pass BOTH:
1. A binary check (EMA crossover, etc.) — existing behavior
2. A score threshold — new conviction filter

The score also modulates position size: higher conviction = larger position.

Phase 1: OHLCV-based signals only (Category A)
Phase 2+: Microstructure signals (funding, OI, taker ratio)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SignalComponent:
    """A single signal's contribution."""

    name: str
    score: float  # -1.0 (strong short) to +1.0 (strong long)
    weight: float
    raw_value: float = 0.0


@dataclass
class CompositeScore:
    """Aggregated multi-signal score."""

    direction: str  # "long", "short", "neutral"
    total_score: float  # weighted sum, roughly -1.0 to +1.0
    conviction: float  # 0.0-1.0 agreement ratio
    components: list = field(default_factory=list)

    @property
    def abs_score(self) -> float:
        """Absolute value of the total score."""
        return abs(self.total_score)


class SignalScorer:
    """Multi-signal weighted scoring engine.

    Computes a composite score from multiple OHLCV-based sub-signals.
    Each sub-signal contributes a value in [-1, +1], weighted and summed.

    The final score gates entries (must exceed entry_threshold) and
    scales position size (linearly from 0.5x at threshold to 1.5x at
    high_conviction).
    """

    # Default weights must sum to 1.0
    # Phase 2 adds funding_rate; when absent the weight is simply unused.
    DEFAULT_WEIGHTS: dict = {
        "ema_alignment": 0.20,
        "momentum_mtf": 0.15,
        "volume_surge": 0.12,
        "rsi_zone": 0.12,
        "atr_regime": 0.08,
        "candle_strength": 0.13,
        "funding_rate": 0.20,  # Orthogonal to OHLCV — high weight justified
    }

    def __init__(self, config: dict) -> None:
        """Initialize the scorer from configuration.

        Args:
            config: Bot configuration dict. The scorer reads the
                    ``signal_scorer`` sub-dict; all keys are optional
                    and fall back to sensible defaults.
        """
        self.config = config
        scorer_cfg = config.get("signal_scorer", {})
        self.enabled: bool = scorer_cfg.get("enabled", False)
        self.entry_threshold: float = scorer_cfg.get("entry_threshold", 0.3)
        self.high_conviction: float = scorer_cfg.get("high_conviction", 0.6)

        weights_cfg = scorer_cfg.get("weights", {})
        # Start from defaults, then overlay any values supplied in config.
        # Also accept weights for keys not in DEFAULT_WEIGHTS (e.g. a custom
        # override that disables funding_rate by setting it to 0).
        merged = dict(self.DEFAULT_WEIGHTS)
        merged.update(weights_cfg)
        self.weights: dict = merged

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        row: pd.Series,
        df: pd.DataFrame,
        signal_direction: str,
    ) -> CompositeScore:
        """Compute composite score for a signal direction.

        Args:
            row: Signal candle (iloc[-2], last closed candle).
            df: Full DataFrame with indicators up to and including *row*.
            signal_direction: "long" or "short".

        Returns:
            CompositeScore with total_score, conviction, and components.
        """
        components: list[SignalComponent] = []

        components.append(self._score_ema_alignment(row, signal_direction))
        components.append(self._score_momentum(row, df, signal_direction))
        components.append(self._score_volume(row, signal_direction))
        components.append(self._score_rsi(row, signal_direction))
        components.append(self._score_atr_regime(row, df))
        components.append(self._score_candle_strength(row, signal_direction))
        components.append(self._score_funding_rate(row, signal_direction))

        # Only include components whose weight > 0 in the total so that
        # configs that zero-out funding_rate are unaffected.
        total = sum(c.score * c.weight for c in components if c.weight > 0)

        # Conviction = fraction of *active* components (weight > 0) agreeing
        # with direction.  Zero-weight components are decorative only.
        active = [c for c in components if c.weight > 0]
        if signal_direction == "long":
            agreeing = sum(1 for c in active if c.score > 0)
        else:
            agreeing = sum(1 for c in active if c.score < 0)
        conviction = agreeing / len(active) if active else 0.0

        direction = "long" if total > 0 else ("short" if total < 0 else "neutral")

        result = CompositeScore(
            direction=direction,
            total_score=total,
            conviction=conviction,
            components=components,
        )

        logger.debug(
            "signal_scorer_result",
            extra={
                "signal_direction": signal_direction,
                "total_score": round(total, 4),
                "conviction": round(conviction, 3),
                "abs_score": round(result.abs_score, 4),
                "components": {c.name: round(c.score, 3) for c in components},
            },
        )

        return result

    def get_size_multiplier(self, score: CompositeScore) -> float:
        """Convert composite score to a position-size multiplier.

        Returns:
            0.0  — score below threshold, skip trade entirely.
            0.5–1.5 — linear scale based on conviction level.
        """
        if not self.enabled:
            return 1.0

        if score.abs_score < self.entry_threshold:
            return 0.0  # Below threshold — skip

        range_size = self.high_conviction - self.entry_threshold
        if range_size <= 0:
            return 1.0

        # Linear interpolation: threshold → 0.5x, high_conviction → 1.5x
        normalized = (score.abs_score - self.entry_threshold) / range_size
        return 0.5 + min(normalized, 1.0) * 1.0  # clamp output to [0.5, 1.5]

    # ------------------------------------------------------------------
    # Sub-signal scorers (Category A — OHLCV only)
    # ------------------------------------------------------------------

    def _score_ema_alignment(
        self, row: pd.Series, direction: str
    ) -> SignalComponent:
        """EMA stack alignment — fast vs slow and higher-timeframe trend.

        Perfect bull alignment (fast > slow AND above 1h trend) = +1.0.
        Each individual alignment contributes +0.5; misalignment deducts.
        """
        score = 0.0

        ema_f = row.get("ema_fast")
        ema_s = row.get("ema_slow")
        raw_diff = 0.0

        if pd.notna(ema_f) and pd.notna(ema_s):
            raw_diff = float(ema_f - ema_s)
            if direction == "long":
                score += 0.5 if ema_f > ema_s else -0.3
            else:
                score += 0.5 if ema_f < ema_s else -0.3

        above = row.get("above_trend")
        below = row.get("below_trend")
        if pd.notna(above):
            if direction == "long":
                score += 0.5 if bool(above) else -0.3
            else:
                score += 0.5 if pd.notna(below) and bool(below) else -0.3

        score = max(-1.0, min(1.0, score))

        # Normalise sign so that a "good" score is always positive for the
        # calling direction — get_size_multiplier uses abs_score.
        final = score if direction == "long" else -abs(score) if score > 0 else abs(score)

        return SignalComponent(
            name="ema_alignment",
            score=max(-1.0, min(1.0, final)),
            weight=self.weights.get("ema_alignment", 0.25),
            raw_value=raw_diff,
        )

    def _score_momentum(
        self, row: pd.Series, df: pd.DataFrame, direction: str
    ) -> SignalComponent:
        """Multi-bar rate-of-change momentum.

        Measures 5-bar and 15-bar ROC. Strong momentum in signal
        direction scores positively.
        """
        score = 0.0
        roc5 = 0.0

        if len(df) >= 6:
            prev5 = df.iloc[-6]["close"]
            if prev5 != 0:
                roc5 = (row["close"] - prev5) / prev5
                contrib = min(abs(roc5) * 50, 0.5)
                score += contrib if (
                    (direction == "long" and roc5 > 0)
                    or (direction == "short" and roc5 < 0)
                ) else -contrib

        if len(df) >= 16:
            prev15 = df.iloc[-16]["close"]
            if prev15 != 0:
                roc15 = (row["close"] - prev15) / prev15
                contrib = min(abs(roc15) * 25, 0.5)
                score += contrib if (
                    (direction == "long" and roc15 > 0)
                    or (direction == "short" and roc15 < 0)
                ) else -contrib

        score = max(-1.0, min(1.0, score))

        return SignalComponent(
            name="momentum_mtf",
            score=score,
            weight=self.weights.get("momentum_mtf", 0.20),
            raw_value=roc5,
        )

    def _score_volume(
        self, row: pd.Series, direction: str
    ) -> SignalComponent:
        """Volume confirmation relative to the 20-bar moving average.

        High volume on a candle aligned with signal direction = strong.
        High volume against direction penalises.
        """
        vol = row.get("volume", 0) or 0
        vol_ma = row.get("volume_ma") or 0
        vol_ratio = vol / max(float(vol_ma), 1e-10) if vol_ma else 1.0

        # Volume above average scores positively, below caps at -0.5
        base_score = min((vol_ratio - 1.0) * 0.5, 1.0)
        base_score = max(base_score, -0.5)

        # Flip sign if candle direction opposes trade direction
        candle_bull = row["close"] > row["open"]
        direction_match = (direction == "long" and candle_bull) or (
            direction == "short" and not candle_bull
        )
        if not direction_match:
            base_score *= -0.5

        return SignalComponent(
            name="volume_surge",
            score=max(-1.0, min(1.0, base_score)),
            weight=self.weights.get("volume_surge", 0.15),
            raw_value=vol_ratio,
        )

    def _score_rsi(
        self, row: pd.Series, direction: str
    ) -> SignalComponent:
        """RSI zone scoring — momentum confirmation, NOT mean-reversion.

        For longs: RSI 50-65 ideal (strong momentum zone) = +0.8.
        For shorts: RSI 35-50 ideal = +0.8.
        Extreme overbought/oversold penalised slightly.
        """
        rsi = row.get("rsi", 50)
        if pd.isna(rsi):
            return SignalComponent(
                name="rsi_zone",
                score=0.0,
                weight=self.weights.get("rsi_zone", 0.15),
            )
        rsi = float(rsi)

        if direction == "long":
            if 50 <= rsi <= 65:
                score = 0.8
            elif 40 <= rsi < 50:
                score = 0.3
            elif 65 < rsi <= 75:
                score = -0.2  # Slightly overbought
            else:
                score = -0.5  # Extreme (< 40 or > 75)
        else:
            if 35 <= rsi <= 50:
                score = 0.8
            elif 50 < rsi <= 60:
                score = 0.3
            elif 25 <= rsi < 35:
                score = -0.2  # Slightly oversold
            else:
                score = -0.5  # Extreme

        return SignalComponent(
            name="rsi_zone",
            score=score,
            weight=self.weights.get("rsi_zone", 0.15),
            raw_value=rsi,
        )

    def _score_atr_regime(
        self, row: pd.Series, df: pd.DataFrame
    ) -> SignalComponent:
        """ATR regime — is volatility in the sweet spot for trend following?

        Moderate ATR relative to 50-bar mean = good.
        Very low ATR (compressed, ranging) = bad.
        Very high ATR (chaotic) = mildly bad.

        Note: this component is direction-neutral; the same score applies
        whether we are going long or short.
        """
        atr = row.get("atr")
        if pd.isna(atr) or atr == 0 or len(df) < 50:
            return SignalComponent(
                name="atr_regime",
                score=0.0,
                weight=self.weights.get("atr_regime", 0.10),
            )

        atr_series = df["atr"].dropna()
        if len(atr_series) < 50:
            return SignalComponent(
                name="atr_regime",
                score=0.0,
                weight=self.weights.get("atr_regime", 0.10),
            )

        atr_ma = atr_series.tail(50).mean()
        if atr_ma == 0:
            return SignalComponent(
                name="atr_regime",
                score=0.0,
                weight=self.weights.get("atr_regime", 0.10),
            )

        ratio = float(atr) / float(atr_ma)

        if 0.8 <= ratio <= 1.3:
            score = 0.5   # Normal volatility — ideal for trend following
        elif ratio < 0.6:
            score = -0.5  # Very compressed — ranging, low edge
        elif ratio > 2.0:
            score = -0.3  # Very volatile — risky entries
        else:
            score = 0.2   # Slightly elevated or slightly compressed

        return SignalComponent(
            name="atr_regime",
            score=score,
            weight=self.weights.get("atr_regime", 0.10),
            raw_value=ratio,
        )

    def _score_candle_strength(
        self, row: pd.Series, direction: str
    ) -> SignalComponent:
        """Signal candle body dominance.

        A large body in the signal direction = strong conviction.
        Doji or body against direction = weak / penalised.
        """
        body = row["close"] - row["open"]
        candle_range = row["high"] - row["low"]

        if candle_range == 0:
            return SignalComponent(
                name="candle_strength",
                score=0.0,
                weight=self.weights.get("candle_strength", 0.15),
            )

        body_pct = abs(body) / candle_range  # 0–1
        is_bull = body > 0

        aligned = (direction == "long" and is_bull) or (
            direction == "short" and not is_bull
        )

        if aligned:
            score = body_pct  # 0 to 1.0
        else:
            score = -body_pct * 0.5  # Penalise less for counter-direction body

        return SignalComponent(
            name="candle_strength",
            score=max(-1.0, min(1.0, score)),
            weight=self.weights.get("candle_strength", 0.15),
            raw_value=body_pct,
        )

    def _score_funding_rate(
        self, row: pd.Series, direction: str
    ) -> SignalComponent:
        """Funding rate as a contrarian / confirmation signal.

        Funding rate is the fee perpetual longs pay shorts (positive) or
        shorts pay longs (negative) every 8 hours.  Extreme rates signal a
        crowded trade and act as a contrarian warning; moderate/opposing
        rates confirm the trade.

        Thresholds (annualised ~3× daily):
            extreme_positive  = +0.03 %  → longs very crowded → bearish bias
            moderate_positive = +0.01 %  → longs mildly crowded
            extreme_negative  = -0.03 %  → shorts very crowded → bullish bias
            moderate_negative = -0.01 %  → shorts mildly crowded

        Scoring for longs (shorts is the mirror):
            funding ≤ -0.03 %  → +0.8   (shorts squeezed, very bullish)
            funding ≤ -0.01 %  → +0.4   (mildly bullish)
            -0.01 % < funding < +0.01 %  → 0.0 (neutral)
            funding ≤ +0.03 %  → -0.3   (longs moderately crowded)
            funding >  +0.03 % → -0.7   (longs very crowded, skip)

        If ``fundingRate`` is absent from the row the component returns 0
        (no opinion) so pre-Phase-2 backtests are unaffected.
        """
        weight = self.weights.get("funding_rate", 0.20)

        funding = row.get("fundingRate", None)
        if funding is None or pd.isna(funding):
            return SignalComponent(
                name="funding_rate",
                score=0.0,
                weight=weight,
                raw_value=0.0,
            )

        funding = float(funding)

        # Thresholds
        EXTREME_POS = 0.0003    # +0.03 %
        MODERATE_POS = 0.0001   # +0.01 %
        EXTREME_NEG = -0.0003   # -0.03 %
        MODERATE_NEG = -0.0001  # -0.01 %

        if direction == "long":
            if funding <= EXTREME_NEG:
                score = 0.8    # Shorts very crowded — contrarian bullish
            elif funding <= MODERATE_NEG:
                score = 0.4    # Shorts mildly crowded — bullish lean
            elif funding < MODERATE_POS:
                score = 0.0    # Neutral band
            elif funding <= EXTREME_POS:
                score = -0.3   # Longs moderately crowded — caution
            else:
                score = -0.7   # Longs very crowded — strong contrarian short
        else:  # short
            if funding >= EXTREME_POS:
                score = 0.8    # Longs very crowded — contrarian bearish
            elif funding >= MODERATE_POS:
                score = 0.4    # Longs mildly crowded — bearish lean
            elif funding > MODERATE_NEG:
                score = 0.0    # Neutral band
            elif funding >= EXTREME_NEG:
                score = -0.3   # Shorts moderately crowded — caution
            else:
                score = -0.7   # Shorts very crowded — strong contrarian long

        return SignalComponent(
            name="funding_rate",
            score=score,
            weight=weight,
            raw_value=funding,
        )
