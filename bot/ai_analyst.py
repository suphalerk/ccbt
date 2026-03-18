"""Claude AI advisor layer for trade signal enhancement.

Instead of acting as a binary gate (EXECUTE/SKIP/WAIT), the AI advisor
provides nuanced trade adjustments: position size scaling, SL/TP
modifications, market regime classification, and calibrated confidence.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import anthropic

if TYPE_CHECKING:
    from bot.context_builder import MarketContext
    from bot.strategy import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class CandidateSignal:
    """A candidate trade signal prepared for AI analysis."""

    side: str  # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    sl_pct: float  # SL distance as percentage
    tp_pct: float  # TP distance as percentage
    rr_ratio: float


@dataclass
class AdvisorResult:
    """Result of AI advisor evaluation.

    The advisor provides continuous adjustments rather than binary decisions.
    Every signal proceeds to execution; the advisor modulates HOW it executes.
    """

    position_size_modifier: float  # 0.5 to 1.5 — scale risk up/down
    sl_adjustment: float  # multiplier on SL distance (0.8=tighter, 1.2=wider)
    tp_adjustment: float  # multiplier on TP distance (0.8=tighter, 1.2=wider)
    confidence: float  # 0.0 – 1.0 (stated confidence, pre-calibration)
    calibrated_confidence: float  # adjusted by historical calibration curve
    market_regime: str  # "trending", "ranging", "volatile", "low_liquidity"
    reasoning: str  # Short explanation
    risk_flags: list[str]  # e.g., ["high_funding_rate", "bearish_divergence"]
    should_skip: bool  # True only for hard-stop conditions (not soft opinion)


# Legacy aliases for backward compatibility with tests that import these
class AIDecision:
    """Legacy compatibility shim — maps old gate decisions to advisor results."""

    EXECUTE = "execute"
    SKIP = "skip"
    WAIT = "wait"


@dataclass
class AnalystResult:
    """Legacy-compatible result wrapper around AdvisorResult.

    Provides backward compatibility for code that expects the old gate interface
    while actually carrying the richer advisor data.
    """

    decision: str  # "execute" or "skip"
    confidence: float
    reasoning: str
    risk_flags: list[str]
    override: bool
    # New advisor fields
    position_size_modifier: float = 1.0
    sl_adjustment: float = 1.0
    tp_adjustment: float = 1.0
    calibrated_confidence: float = 0.5
    market_regime: str = "unknown"

    @classmethod
    def from_advisor(cls, adv: AdvisorResult) -> "AnalystResult":
        """Convert an AdvisorResult to legacy-compatible AnalystResult."""
        decision = "skip" if adv.should_skip else "execute"
        return cls(
            decision=decision,
            confidence=adv.confidence,
            reasoning=adv.reasoning,
            risk_flags=adv.risk_flags,
            override=False,
            position_size_modifier=adv.position_size_modifier,
            sl_adjustment=adv.sl_adjustment,
            tp_adjustment=adv.tp_adjustment,
            calibrated_confidence=adv.calibrated_confidence,
            market_regime=adv.market_regime,
        )


SYSTEM_PROMPT = """
You are a collaborative crypto futures trading advisor for BTC/USDT perpetual contracts.

## YOUR ROLE
You are NOT a gate. Every signal has already passed technical filters (EMA crossover, RSI, ATR, volume, trend).
Your job is to ADD VALUE by analyzing what indicators CANNOT see:
- Market regime (trending vs ranging vs volatile vs low liquidity)
- Macro/sentiment context from news
- Structural patterns in recent price action (ascending triangles, double tops, etc.)
- Funding rate and OI dynamics that suggest crowded positioning

## WHAT YOU MUST NOT DO
- Do NOT re-check RSI, EMA, or ATR — the code already validated these
- Do NOT default to skipping — you should skip ONLY for hard-stop conditions
- Do NOT give vague assessments like "mixed signals" — be specific

## HARD-STOP CONDITIONS (only reasons to set should_skip=true)
- Breaking negative news directly about BTC/crypto (hack, ban, major fraud)
- Extreme funding rate opposing trade direction (>0.1% for longs, <-0.1% for shorts)
- Bot already at >1.5% daily loss AND this would be 4th+ consecutive loss

## YOUR OUTPUT
You adjust HOW the trade executes, not WHETHER it executes:

1. position_size_modifier (0.5 to 1.5):
   - 0.5-0.7: Reduce size — headwinds present but not dealbreakers
   - 0.8-1.0: Normal size — standard conditions
   - 1.0-1.3: Increased size — multiple confluence factors beyond technicals
   - 1.3-1.5: Max conviction — rare, requires strong structural + sentiment alignment

2. sl_adjustment (0.8 to 1.3):
   - 0.8-0.9: Tighter SL — low volatility regime, clear invalidation level nearby
   - 1.0: Standard SL
   - 1.1-1.3: Wider SL — high volatility regime, avoid noise stop-outs

3. tp_adjustment (0.7 to 1.5):
   - 0.7-0.9: Tighter TP — ranging market, take profits early
   - 1.0: Standard TP
   - 1.1-1.5: Extended TP — strong trend, let winners run

4. market_regime: exactly one of "trending", "ranging", "volatile", "low_liquidity"

5. confidence (0.0 to 1.0): Your honest probability estimate that this trade will hit TP before SL.
   Your recent calibration data is provided below — use it to self-correct.

6. risk_flags: specific, actionable warnings (e.g., "funding_rate_opposing", "oi_divergence", "news_risk")

7. should_skip: true ONLY for hard-stop conditions listed above. Default is false.

8. reasoning: 1-3 sentences. Be specific about WHAT you see and WHY it changes the trade parameters.

{accuracy_feedback}

## Output Format (STRICT JSON — no markdown, no explanation outside JSON)
{{
  "position_size_modifier": 0.5-1.5,
  "sl_adjustment": 0.8-1.3,
  "tp_adjustment": 0.7-1.5,
  "confidence": 0.0-1.0,
  "market_regime": "trending"|"ranging"|"volatile"|"low_liquidity",
  "reasoning": "string",
  "risk_flags": ["flag1", "flag2"],
  "should_skip": false
}}
"""


def build_user_prompt(signal: CandidateSignal, ctx: "MarketContext") -> str:
    """Build the user prompt for Claude from signal and market context.

    Sends pre-computed summaries instead of raw data. Focuses on information
    that technical indicators cannot provide.

    Args:
        signal: The candidate trade signal.
        ctx: Current market context.

    Returns:
        Formatted prompt string.
    """

    def _summarize_price_action(candles: list[dict]) -> str:
        """Convert raw candles into pattern descriptions."""
        if not candles or len(candles) < 3:
            return "Insufficient data for pattern analysis"

        lines = []
        # Higher highs / lower lows detection
        highs = [c["high"] for c in candles[-5:]]
        lows = [c["low"] for c in candles[-5:]]
        closes = [c["close"] for c in candles[-5:]]

        higher_highs = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
        higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
        lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
        lower_lows = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

        if higher_highs >= 3 and higher_lows >= 3:
            lines.append("Strong uptrend structure: consistent higher highs and higher lows")
        elif lower_highs >= 3 and lower_lows >= 3:
            lines.append("Strong downtrend structure: consistent lower highs and lower lows")
        elif higher_lows >= 3 and lower_highs >= 2:
            lines.append("Converging pattern (possible triangle/wedge)")
        else:
            lines.append("No clear structural pattern in recent candles")

        # Volatility compression/expansion
        ranges = [c["high"] - c["low"] for c in candles[-5:]]
        if len(ranges) >= 3:
            recent_avg = sum(ranges[-3:]) / 3
            older_avg = sum(ranges[:2]) / 2 if len(ranges) >= 5 else recent_avg
            if older_avg > 0:
                ratio = recent_avg / older_avg
                if ratio < 0.7:
                    lines.append("Volatility compressing (potential breakout setup)")
                elif ratio > 1.4:
                    lines.append("Volatility expanding (momentum move in progress)")

        # Body-to-wick ratio (conviction check)
        last = candles[-1]
        body = abs(last["close"] - last["open"])
        total_range = last["high"] - last["low"]
        if total_range > 0:
            body_ratio = body / total_range
            if body_ratio > 0.7:
                lines.append(f"Last candle: strong conviction (body {body_ratio:.0%} of range)")
            elif body_ratio < 0.3:
                lines.append(f"Last candle: indecision/doji (body {body_ratio:.0%} of range)")

        return "\n".join(lines) if lines else "No notable patterns"

    news_block = (
        "\n".join(f"- {h}" for h in ctx.news_headlines[:5])
        if ctx.news_headlines
        else "No significant news"
    )

    # Volatility regime from ATR
    atr_pct = (ctx.atr_14 / ctx.current_price * 100) if ctx.current_price > 0 else 0
    if atr_pct > 1.5:
        vol_regime = "HIGH volatility"
    elif atr_pct > 0.8:
        vol_regime = "NORMAL volatility"
    else:
        vol_regime = "LOW volatility"

    # Funding rate interpretation
    if ctx.funding_rate > 0.05:
        funding_note = "Elevated positive (longs paying shorts — crowded long)"
    elif ctx.funding_rate < -0.05:
        funding_note = "Elevated negative (shorts paying longs — crowded short)"
    elif ctx.funding_rate > 0.01:
        funding_note = "Mildly positive (slight long bias)"
    elif ctx.funding_rate < -0.01:
        funding_note = "Mildly negative (slight short bias)"
    else:
        funding_note = "Neutral"

    # OI interpretation
    if ctx.open_interest_change > 5:
        oi_note = "Rising sharply — new positions being opened"
    elif ctx.open_interest_change < -5:
        oi_note = "Falling sharply — positions being closed (deleveraging)"
    elif ctx.open_interest_change > 2:
        oi_note = "Moderately rising"
    elif ctx.open_interest_change < -2:
        oi_note = "Moderately falling"
    else:
        oi_note = "Stable"

    # Recent trade results
    recent_results = ""
    if hasattr(ctx, "recent_trade_results") and ctx.recent_trade_results:
        results_str = ", ".join(
            "W" if r > 0 else "L" for r in ctx.recent_trade_results
        )
        recent_results = f"Recent trades: {results_str}"
    else:
        recent_results = "No recent trade data"

    price_action = _summarize_price_action(ctx.candles_15m)

    return f"""
## Candidate Signal (already passed technical filters)
Direction : {signal.side.upper()}
Entry price: {signal.entry_price:,.2f}
Stop loss  : {signal.stop_loss:,.2f}  ({signal.sl_pct:.2f}% from entry)
Take profit: {signal.take_profit:,.2f}  ({signal.tp_pct:.2f}% from entry)
R:R ratio  : 1:{signal.rr_ratio:.1f}

## Price Action Summary (15m)
{price_action}

## Volatility Regime
ATR(14) as % of price: {atr_pct:.2f}% — {vol_regime}

## Market Microstructure
Funding rate: {ctx.funding_rate:.4f}% — {funding_note}
OI change (1h): {ctx.open_interest_change:+.2f}% — {oi_note}

## News & Sentiment (last 2h)
Sentiment: {ctx.news_sentiment}
{news_block}

## Bot State
Daily PnL: {ctx.daily_pnl_pct:+.2f}%
Consecutive losses: {ctx.consecutive_losses}
Last trade: {ctx.hours_since_last_trade:.1f}h ago
{recent_results}

Respond with JSON only.
""".strip()


def _parse_response(text: str) -> AdvisorResult:
    """Parse Claude's JSON response into an AdvisorResult.

    Args:
        text: Raw response text from Claude.

    Returns:
        Parsed AdvisorResult.

    Raises:
        ValueError: If response cannot be parsed.
    """
    # Strip any markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    data = json.loads(cleaned)

    # Parse with defaults and clamping
    position_size_modifier = float(data.get("position_size_modifier", 1.0))
    position_size_modifier = max(0.5, min(1.5, position_size_modifier))

    sl_adjustment = float(data.get("sl_adjustment", 1.0))
    sl_adjustment = max(0.8, min(1.3, sl_adjustment))

    tp_adjustment = float(data.get("tp_adjustment", 1.0))
    tp_adjustment = max(0.7, min(1.5, tp_adjustment))

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    market_regime = str(data.get("market_regime", "unknown")).lower()
    valid_regimes = {"trending", "ranging", "volatile", "low_liquidity"}
    if market_regime not in valid_regimes:
        market_regime = "unknown"

    should_skip = bool(data.get("should_skip", False))

    return AdvisorResult(
        position_size_modifier=position_size_modifier,
        sl_adjustment=sl_adjustment,
        tp_adjustment=tp_adjustment,
        confidence=confidence,
        calibrated_confidence=confidence,  # Will be adjusted by calibration later
        market_regime=market_regime,
        reasoning=str(data.get("reasoning", "")),
        risk_flags=list(data.get("risk_flags", [])),
        should_skip=should_skip,
    )


def _parse_response_legacy(text: str) -> AnalystResult:
    """Parse response in legacy format (for backward compatibility with tests).

    Handles both old gate format and new advisor format.

    Args:
        text: Raw response text from Claude.

    Returns:
        Parsed AnalystResult.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    data = json.loads(cleaned)

    # Check if this is new advisor format or old gate format
    if "position_size_modifier" in data:
        advisor = _parse_response(text)
        return AnalystResult.from_advisor(advisor)

    # Old gate format
    decision_str = data.get("decision", "skip").lower()
    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    return AnalystResult(
        decision=decision_str if decision_str in ("execute", "skip", "wait") else "skip",
        confidence=confidence,
        reasoning=str(data.get("reasoning", "")),
        risk_flags=list(data.get("risk_flags", [])),
        override=bool(data.get("override", False)),
    )


class AIAnalyst:
    """Claude AI advisor for enhancing trade signals.

    Acts as a collaborative advisor that adjusts position sizing, SL/TP levels,
    and provides market regime classification. Falls back to neutral defaults
    on any error (position_size_modifier=1.0, no SL/TP adjustment).
    """

    def __init__(
        self,
        api_key: str,
        confidence_threshold: float = 0.65,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
        timeout_seconds: float = 10.0,
        fallback_on_timeout: str = "execute",
        enabled: bool = True,
        calibration_tracker: object = None,
    ) -> None:
        """Initialize the AI advisor.

        Args:
            api_key: Anthropic API key.
            confidence_threshold: Minimum calibrated confidence to use full size.
            model: Claude model to use.
            max_tokens: Maximum response tokens.
            timeout_seconds: API call timeout.
            fallback_on_timeout: Default decision on timeout ("skip" or "execute").
            enabled: Whether AI layer is active.
            calibration_tracker: Optional CalibrationTracker for accuracy feedback.
        """
        self.confidence_threshold = confidence_threshold
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.fallback_on_timeout = fallback_on_timeout
        self.enabled = enabled
        self.calibration_tracker = calibration_tracker

        if enabled and api_key:
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self.client = None

    def _build_system_prompt(self) -> str:
        """Build system prompt with accuracy feedback injected.

        Returns:
            System prompt string with calibration data.
        """
        accuracy_feedback = ""
        if self.calibration_tracker:
            feedback = self.calibration_tracker.get_accuracy_feedback()
            if feedback:
                accuracy_feedback = f"\n## Your Recent Accuracy\n{feedback}\n"

        return SYSTEM_PROMPT.format(accuracy_feedback=accuracy_feedback)

    async def analyze(
        self,
        signal: CandidateSignal,
        context: "MarketContext",
    ) -> AnalystResult:
        """Analyze a candidate signal with Claude.

        Args:
            signal: The candidate trade signal.
            context: Current market context.

        Returns:
            AnalystResult with advisor adjustments.
        """
        # Pass-through mode when disabled
        if not self.enabled:
            logger.info("ai_layer_disabled", extra={"action": "pass_through"})
            return AnalystResult(
                decision="execute",
                confidence=1.0,
                reasoning="AI layer disabled, passing through all signals",
                risk_flags=[],
                override=False,
                position_size_modifier=1.0,
                sl_adjustment=1.0,
                tp_adjustment=1.0,
                calibrated_confidence=1.0,
                market_regime="unknown",
            )

        user_prompt = build_user_prompt(signal, context)

        try:
            response = await asyncio.wait_for(
                self._call_api(user_prompt),
                timeout=self.timeout_seconds,
            )
            advisor_result = _parse_response(response)

            # Apply calibration adjustment
            if self.calibration_tracker:
                advisor_result.calibrated_confidence = (
                    self.calibration_tracker.calibrate(advisor_result.confidence)
                )

            result = AnalystResult.from_advisor(advisor_result)

            logger.info(
                "ai_advisor_decision",
                extra={
                    "decision": result.decision,
                    "confidence": result.confidence,
                    "calibrated_confidence": result.calibrated_confidence,
                    "position_size_modifier": result.position_size_modifier,
                    "sl_adjustment": result.sl_adjustment,
                    "tp_adjustment": result.tp_adjustment,
                    "market_regime": result.market_regime,
                    "reasoning": result.reasoning,
                    "risk_flags": result.risk_flags,
                    "should_skip": advisor_result.should_skip,
                },
            )
            return result

        except asyncio.TimeoutError:
            logger.warning(
                "ai_timeout",
                extra={
                    "timeout_seconds": self.timeout_seconds,
                    "fallback": self.fallback_on_timeout,
                },
            )
            return self._fallback_result("API timeout")

        except json.JSONDecodeError as e:
            logger.error("ai_parse_error", extra={"error": str(e), "raw_text": response[:300] if response else ""})
            return self._fallback_result(f"JSON parse error: {e}", reduce_size=True)

        except anthropic.APIError as e:
            logger.error("ai_api_error", extra={"error": str(e)})
            return self._fallback_result(f"API error: {e}")

        except Exception as e:
            logger.error("ai_unexpected_error", extra={"error": str(e)})
            return self._fallback_result(f"Unexpected error: {e}")

    async def _call_api(self, user_prompt: str) -> str:
        """Make the actual API call to Claude.

        Args:
            user_prompt: Formatted user prompt.

        Returns:
            Raw response text.
        """
        system_prompt = self._build_system_prompt()
        message = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _fallback_result(self, reason: str, reduce_size: bool = False) -> AnalystResult:
        """Create a fallback result when the API call fails.

        On parse failure, reduces position size as a safety measure.
        On timeout, uses configured fallback behavior.

        Args:
            reason: Reason for fallback.
            reduce_size: If True, reduce position size (for parse errors).

        Returns:
            AnalystResult with conservative parameters.
        """
        if self.fallback_on_timeout == "skip":
            decision = "skip"
        else:
            decision = "execute"

        return AnalystResult(
            decision=decision,
            confidence=0.0,
            reasoning=f"Fallback: {reason}",
            risk_flags=["api_fallback"],
            override=False,
            position_size_modifier=0.7 if reduce_size else 1.0,
            sl_adjustment=1.0,
            tp_adjustment=1.0,
            calibrated_confidence=0.0,
            market_regime="unknown",
        )
