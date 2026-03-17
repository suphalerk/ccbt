"""Tests for AI analyst layer."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.ai_analyst import (
    AIAnalyst,
    AIDecision,
    AnalystResult,
    CandidateSignal,
    _parse_response,
    build_user_prompt,
)
from bot.context_builder import MarketContext


@pytest.fixture
def config():
    """AI layer test config."""
    return {
        "ai_layer": {
            "enabled": True,
            "confidence_threshold": 0.65,
            "model": "claude-sonnet-4-6",
            "max_tokens": 256,
            "timeout_seconds": 8,
            "fallback_on_timeout": "skip",
        }
    }


@pytest.fixture
def candidate_signal():
    """Sample candidate signal."""
    return CandidateSignal(
        side="long",
        entry_price=65000.0,
        stop_loss=64350.0,
        take_profit=66300.0,
        sl_pct=1.0,
        tp_pct=2.0,
        rr_ratio=2.0,
    )


@pytest.fixture
def market_context():
    """Sample market context."""
    return MarketContext(
        symbol="BTCUSDT",
        current_price=65000.0,
        candles_15m=[
            {"open": 64900, "high": 65100, "low": 64800, "close": 65000, "volume": 200}
        ],
        candles_1h=[
            {"open": 64500, "high": 65200, "low": 64400, "close": 65000, "volume": 1000}
        ],
        ema_9=65050.0,
        ema_21=64900.0,
        ema_50_1h=64500.0,
        rsi_14=55.0,
        atr_14=450.0,
        volume_ratio=1.3,
        funding_rate=0.0005,
        open_interest_change=2.5,
        bid_ask_spread=0.0001,
        orderbook_imbalance=0.58,
        news_headlines=["Bitcoin breaks above $65K resistance level"],
        news_sentiment="positive",
        daily_pnl_pct=0.3,
        consecutive_losses=0,
        hours_since_last_trade=2.5,
    )


class TestParseResponse:
    """Test JSON response parsing."""

    def test_valid_execute_response(self):
        """Should parse a valid EXECUTE response."""
        text = json.dumps({
            "decision": "execute",
            "confidence": 0.85,
            "reasoning": "Strong technical confluence with trend support",
            "risk_flags": [],
            "override": False,
        })
        result = _parse_response(text)
        assert result.decision == AIDecision.EXECUTE
        assert result.confidence == 0.85
        assert result.override is False

    def test_valid_skip_response(self):
        """Should parse a valid SKIP response."""
        text = json.dumps({
            "decision": "skip",
            "confidence": 0.3,
            "reasoning": "Extreme funding rate opposing signal",
            "risk_flags": ["high_funding_rate"],
            "override": False,
        })
        result = _parse_response(text)
        assert result.decision == AIDecision.SKIP
        assert "high_funding_rate" in result.risk_flags

    def test_valid_wait_response(self):
        """Should parse a valid WAIT response."""
        text = json.dumps({
            "decision": "wait",
            "confidence": 0.5,
            "reasoning": "Mixed signals, need more data",
            "risk_flags": ["mixed_signals"],
            "override": False,
        })
        result = _parse_response(text)
        assert result.decision == AIDecision.WAIT

    def test_confidence_clamped(self):
        """Confidence should be clamped to 0-1 range."""
        text = json.dumps({
            "decision": "execute",
            "confidence": 1.5,
            "reasoning": "test",
            "risk_flags": [],
            "override": False,
        })
        result = _parse_response(text)
        assert result.confidence == 1.0

    def test_confidence_clamped_negative(self):
        """Negative confidence should be clamped to 0."""
        text = json.dumps({
            "decision": "execute",
            "confidence": -0.5,
            "reasoning": "test",
            "risk_flags": [],
            "override": False,
        })
        result = _parse_response(text)
        assert result.confidence == 0.0

    def test_unknown_decision_defaults_skip(self):
        """Unknown decision should default to SKIP."""
        text = json.dumps({
            "decision": "unknown",
            "confidence": 0.5,
            "reasoning": "test",
            "risk_flags": [],
            "override": False,
        })
        result = _parse_response(text)
        assert result.decision == AIDecision.SKIP

    def test_markdown_code_fence_stripped(self):
        """Should handle response wrapped in markdown code fences."""
        inner = json.dumps({
            "decision": "execute",
            "confidence": 0.8,
            "reasoning": "test",
            "risk_flags": [],
            "override": False,
        })
        text = f"```json\n{inner}\n```"
        result = _parse_response(text)
        assert result.decision == AIDecision.EXECUTE

    def test_invalid_json_raises(self):
        """Invalid JSON should raise ValueError."""
        with pytest.raises(json.JSONDecodeError):
            _parse_response("not json at all")


class TestBuildUserPrompt:
    """Test prompt building."""

    def test_prompt_contains_signal_info(self, candidate_signal, market_context):
        """Prompt should contain signal direction and prices."""
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "LONG" in prompt
        assert "65,000.00" in prompt or "65000" in prompt

    def test_prompt_contains_indicators(self, candidate_signal, market_context):
        """Prompt should contain technical indicator values."""
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "RSI(14)" in prompt
        assert "EMA(9/21)" in prompt

    def test_prompt_contains_funding_rate(self, candidate_signal, market_context):
        """Prompt should contain funding rate."""
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "Funding rate" in prompt

    def test_prompt_contains_news(self, candidate_signal, market_context):
        """Prompt should contain news headlines."""
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "Bitcoin breaks above" in prompt

    def test_prompt_no_news(self, candidate_signal, market_context):
        """Prompt should handle empty news gracefully."""
        market_context.news_headlines = []
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "No significant news" in prompt

    def test_prompt_contains_bot_state(self, candidate_signal, market_context):
        """Prompt should contain bot state info."""
        prompt = build_user_prompt(candidate_signal, market_context)
        assert "Daily PnL" in prompt
        assert "Consec. losses" in prompt


class TestAIAnalystDisabled:
    """Test AI analyst in disabled mode."""

    @pytest.mark.asyncio
    async def test_disabled_passes_through(self, candidate_signal, market_context):
        """Disabled AI should pass through all signals as EXECUTE."""
        analyst = AIAnalyst(api_key="", enabled=False)
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.EXECUTE
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_disabled_no_api_call(self, candidate_signal, market_context):
        """Disabled AI should not make any API calls."""
        analyst = AIAnalyst(api_key="", enabled=False)
        assert analyst.client is None
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.EXECUTE


class TestAIAnalystFallback:
    """Test fallback behavior on errors."""

    @pytest.mark.asyncio
    async def test_timeout_fallback_skip(self, candidate_signal, market_context):
        """Timeout should fallback to SKIP by default."""
        analyst = AIAnalyst(
            api_key="test",
            enabled=True,
            timeout_seconds=0.001,
            fallback_on_timeout="skip",
        )

        # Mock the API call to be slow
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(10)

        analyst._call_api = slow_call
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.SKIP
        assert "api_fallback" in result.risk_flags

    @pytest.mark.asyncio
    async def test_json_error_fallback(self, candidate_signal, market_context):
        """JSON parse error should fallback gracefully."""
        analyst = AIAnalyst(api_key="test", enabled=True)

        async def bad_response(*args, **kwargs):
            return "this is not json"

        analyst._call_api = bad_response
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.SKIP
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_api_error_fallback(self, candidate_signal, market_context):
        """API error should fallback gracefully."""
        analyst = AIAnalyst(api_key="test", enabled=True)

        import anthropic

        async def error_response(*args, **kwargs):
            raise anthropic.APIError(
                message="rate limited",
                request=MagicMock(),
                body=None,
            )

        analyst._call_api = error_response
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.SKIP

    @pytest.mark.asyncio
    async def test_successful_api_call(self, candidate_signal, market_context):
        """Successful API call should return parsed result."""
        analyst = AIAnalyst(api_key="test", enabled=True)

        response_json = json.dumps({
            "decision": "execute",
            "confidence": 0.82,
            "reasoning": "Strong trend confluence, supportive orderbook",
            "risk_flags": [],
            "override": False,
        })

        async def mock_call(*args, **kwargs):
            return response_json

        analyst._call_api = mock_call
        result = await analyst.analyze(candidate_signal, market_context)
        assert result.decision == AIDecision.EXECUTE
        assert result.confidence == 0.82
        assert "Strong trend" in result.reasoning


class TestAIDecisionEnum:
    """Test AIDecision enum values."""

    def test_execute_value(self):
        assert AIDecision.EXECUTE.value == "execute"

    def test_skip_value(self):
        assert AIDecision.SKIP.value == "skip"

    def test_wait_value(self):
        assert AIDecision.WAIT.value == "wait"


class TestCandidateSignal:
    """Test CandidateSignal dataclass."""

    def test_creation(self):
        """Should create a valid CandidateSignal."""
        signal = CandidateSignal(
            side="short",
            entry_price=60000.0,
            stop_loss=60900.0,
            take_profit=58200.0,
            sl_pct=1.5,
            tp_pct=3.0,
            rr_ratio=2.0,
        )
        assert signal.side == "short"
        assert signal.rr_ratio == 2.0
