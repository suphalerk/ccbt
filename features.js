/**
 * CCBT — Crypto Trading Bot Feature Registry
 *
 * Central registry of all features, their status, and metadata.
 * Used for tracking development progress and feature flags.
 */

const FEATURE_STATUS = {
  DONE: 'done',
  IN_PROGRESS: 'in_progress',
  PLANNED: 'planned',
  BLOCKED: 'blocked',
};

const PRIORITY = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
};

const features = {
  // ============================================================
  // PHASE 1-2: Core Trading Engine
  // ============================================================
  core: {
    name: 'Core Trading Engine',
    status: FEATURE_STATUS.DONE,
    features: {
      ema_crossover_signal: {
        name: 'EMA Crossover Signal',
        description: 'EMA(9)/EMA(21) crossover with EMA(50) trend filter on 1h',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
        config_keys: ['ema_fast', 'ema_slow', 'ema_trend'],
      },
      rsi_confirmation: {
        name: 'RSI Confirmation',
        description: 'RSI(14) zone filtering — Long: 48-75, Short: 25-52',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
        config_keys: ['rsi_period', 'rsi_min', 'rsi_max'],
      },
      atr_based_stops: {
        name: 'ATR-Based SL/TP/Trail',
        description: 'Dynamic stop loss (1.5×ATR), take profit (3.0×ATR), trailing (1.8×ATR)',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
        config_keys: ['atr_period', 'atr_sl_mult', 'atr_tp_mult', 'atr_trail_mult'],
      },
      volume_filter: {
        name: 'Volume Filter',
        description: 'Volume must exceed MA(20) × multiplier for signal confirmation',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
        config_keys: ['volume_mult'],
      },
      market_regime_detection: {
        name: 'Market Regime Detection',
        description: 'Classifies market as trending, ranging, or volatile using ATR ratio + directional structure',
        status: FEATURE_STATUS.DONE,
        module: 'bot/data.py',
      },
      trailing_stop: {
        name: 'Trailing Stop',
        description: 'ATR-based trailing stop that ratchets only in profit direction',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
      },
      commission_adjusted_rr: {
        name: 'Commission-Adjusted R:R',
        description: 'Net R:R validation after commission (0.055%) + slippage (0.02%)',
        status: FEATURE_STATUS.DONE,
        module: 'bot/strategy.py',
        config_keys: ['commission_rate', 'slippage_rate', 'min_rr_ratio'],
      },
    },
  },

  // ============================================================
  // PHASE 3: Exchange Integration
  // ============================================================
  exchange: {
    name: 'Exchange Integration',
    status: FEATURE_STATUS.DONE,
    features: {
      bybit_ccxt: {
        name: 'Bybit via ccxt',
        description: 'Full Bybit perpetual futures integration with ccxt library',
        status: FEATURE_STATUS.DONE,
        module: 'bot/exchange.py',
      },
      symbol_normalization: {
        name: 'Symbol Normalization',
        description: 'Auto-converts BTCUSDT → BTC/USDT:USDT for ccxt compatibility',
        status: FEATURE_STATUS.DONE,
        module: 'bot/exchange.py',
      },
      rate_limiting: {
        name: 'Rate Limiting',
        description: '10 req/sec enforced with 100ms delays between API calls',
        status: FEATURE_STATUS.DONE,
        module: 'bot/exchange.py',
      },
      retry_logic: {
        name: 'Retry with Backoff',
        description: 'Exponential backoff for RateLimit, Network, and Exchange errors',
        status: FEATURE_STATUS.DONE,
        module: 'bot/exchange.py',
      },
      sl_verification: {
        name: 'SL Verification',
        description: 'Verifies stop loss was set on exchange with 3 retries',
        status: FEATURE_STATUS.DONE,
        module: 'main.py',
      },
      testnet_support: {
        name: 'Testnet/Live Toggle',
        description: 'Switch between testnet and live via config flag',
        status: FEATURE_STATUS.DONE,
        module: 'bot/exchange.py',
        config_keys: ['use_testnet'],
      },
    },
  },

  // ============================================================
  // PHASE 4: AI Advisor Layer
  // ============================================================
  ai_advisor: {
    name: 'AI Advisor Layer',
    status: FEATURE_STATUS.DONE,
    features: {
      claude_integration: {
        name: 'Claude AI Integration',
        description: 'Anthropic SDK integration with structured system prompts',
        status: FEATURE_STATUS.DONE,
        module: 'bot/ai_analyst.py',
        config_keys: ['ai_layer.model', 'ai_layer.max_tokens'],
      },
      advisor_mode: {
        name: 'Advisor Mode',
        description: 'Nuanced adjustments (position size, SL, TP) instead of binary gate',
        status: FEATURE_STATUS.DONE,
        module: 'bot/ai_analyst.py',
        config_keys: ['ai_layer.mode'],
      },
      position_size_modifier: {
        name: 'Position Size Modifier',
        description: 'AI adjusts position size 0.5-1.5× based on market analysis',
        status: FEATURE_STATUS.DONE,
        module: 'bot/ai_analyst.py',
      },
      sl_tp_adjustments: {
        name: 'SL/TP Adjustments',
        description: 'AI tweaks stop loss and take profit multipliers',
        status: FEATURE_STATUS.DONE,
        module: 'bot/ai_analyst.py',
      },
      calibration_system: {
        name: 'Calibration System',
        description: 'Rolling accuracy tracking with auto-adjusted AI influence multiplier',
        status: FEATURE_STATUS.DONE,
        module: 'bot/logger.py',
        config_keys: ['ai_layer.calibration'],
      },
      market_context: {
        name: 'Market Context Builder',
        description: 'Assembles candles, indicators, funding rate, OI, news, trade history for AI',
        status: FEATURE_STATUS.DONE,
        module: 'bot/context_builder.py',
      },
      news_integration: {
        name: 'News Integration',
        description: 'RSS (Cointelegraph, Decrypt, CoinDesk) + CryptoPanic API',
        status: FEATURE_STATUS.DONE,
        module: 'bot/news_fetcher.py',
        config_keys: ['ai_layer.news_source', 'ai_layer.news_lookback_hours'],
      },
      timeout_fallback: {
        name: 'Timeout Fallback',
        description: '10s timeout with configurable fallback (execute/skip)',
        status: FEATURE_STATUS.DONE,
        module: 'bot/ai_analyst.py',
        config_keys: ['ai_layer.timeout_seconds', 'ai_layer.fallback_on_timeout'],
      },
    },
  },

  // ============================================================
  // PHASE 5: Risk Management
  // ============================================================
  risk_management: {
    name: 'Risk Management',
    status: FEATURE_STATUS.DONE,
    features: {
      position_sizing: {
        name: 'Dynamic Position Sizing',
        description: '1% risk per trade with dynamic adjustment based on win rate + regime',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['risk_per_trade'],
      },
      daily_loss_limit: {
        name: 'Daily Loss Limit',
        description: '3% max daily loss triggers trading halt',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['max_daily_loss'],
      },
      consecutive_loss_cooldown: {
        name: 'Consecutive Loss Cooldown',
        description: '5 consecutive losses triggers 1-hour cooldown',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['max_consecutive_losses', 'cooldown_hours'],
      },
      max_positions: {
        name: 'Max Concurrent Positions',
        description: 'Limit to 2 simultaneous positions (long + short)',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['max_positions'],
      },
      api_error_breaker: {
        name: 'API Error Circuit Breaker',
        description: '3 consecutive API errors triggers halt',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['max_api_errors'],
      },
      leverage_limit: {
        name: 'Leverage Limit',
        description: 'Config-level leverage cap validated on load',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
        config_keys: ['leverage'],
      },
      state_restoration: {
        name: 'State Restoration',
        description: 'Resume risk state (losses, PnL, cooldowns) from DB on restart',
        status: FEATURE_STATUS.DONE,
        module: 'bot/risk.py',
      },
      graceful_shutdown: {
        name: 'Graceful Shutdown',
        description: 'Close all positions + cancel orders on SIGINT/SIGTERM',
        status: FEATURE_STATUS.DONE,
        module: 'main.py',
      },
    },
  },

  // ============================================================
  // PHASE 5: Dashboard
  // ============================================================
  dashboard: {
    name: 'Streamlit Dashboard',
    status: FEATURE_STATUS.DONE,
    features: {
      account_overview: {
        name: 'Account Overview',
        description: 'Balance, PnL, positions, mode, AI status, bot status',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/app.py',
      },
      candlestick_charts: {
        name: 'Interactive Candlestick Charts',
        description: 'Plotly candlestick with EMA(9,21) overlay + trade markers',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/components.py',
      },
      indicator_subplots: {
        name: 'RSI/ATR/Volume Subplots',
        description: 'RSI with zones, ATR volatility, volume with MA(20)',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/components.py',
      },
      trade_log: {
        name: 'Trade Log',
        description: 'Full trade history table with PnL, duration, AI decision, close reason',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/app.py',
      },
      equity_curve: {
        name: 'Equity Curve',
        description: 'Cumulative PnL chart over time',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/components.py',
      },
      performance_metrics: {
        name: 'Performance Metrics',
        description: 'Win rate, Sharpe ratio, profit factor (7-day rolling + all-time)',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/app.py',
      },
      ai_accuracy_analysis: {
        name: 'AI Accuracy Analysis',
        description: 'Confidence histograms, calibration curves, regime accuracy',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/components.py',
      },
      risk_monitor: {
        name: 'Risk Monitor',
        description: 'Daily loss bar, leverage gauge, circuit breaker status',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/components.py',
      },
      auto_refresh: {
        name: 'Auto Refresh',
        description: '30-second auto-refresh cycle via st.rerun()',
        status: FEATURE_STATUS.DONE,
        module: 'dashboard/app.py',
      },
    },
  },

  // ============================================================
  // PHASE 5: Backtesting
  // ============================================================
  backtesting: {
    name: 'Backtesting Engine',
    status: FEATURE_STATUS.DONE,
    features: {
      event_driven_engine: {
        name: 'Event-Driven Engine',
        description: 'Realistic simulation with commission + slippage',
        status: FEATURE_STATUS.DONE,
        module: 'backtest/engine.py',
      },
      risk_compliance: {
        name: 'Risk Compliance',
        description: 'Applies same risk rules as live trading',
        status: FEATURE_STATUS.DONE,
        module: 'backtest/engine.py',
      },
      performance_metrics: {
        name: 'Performance Metrics',
        description: 'Sharpe ratio, max drawdown, profit factor, win rate',
        status: FEATURE_STATUS.DONE,
        module: 'backtest/metrics.py',
      },
    },
  },

  // ============================================================
  // PHASE 6: Deployment
  // ============================================================
  deployment: {
    name: 'Production Deployment',
    status: FEATURE_STATUS.DONE,
    features: {
      docker: {
        name: 'Docker Setup',
        description: 'Multi-stage build, non-root user, health checks',
        status: FEATURE_STATUS.DONE,
        module: 'Dockerfile',
      },
      docker_compose: {
        name: 'Docker Compose',
        description: 'Bot + Dashboard services with shared volume',
        status: FEATURE_STATUS.DONE,
        module: 'docker-compose.yml',
      },
      nginx_proxy: {
        name: 'Nginx Reverse Proxy',
        description: 'HTTPS, basic auth, WebSocket, rate limiting',
        status: FEATURE_STATUS.DONE,
        module: 'deploy/nginx.conf',
      },
      ssl_certbot: {
        name: 'SSL/Certbot',
        description: "Let's Encrypt auto-renewal",
        status: FEATURE_STATUS.DONE,
        module: 'deploy/setup.sh',
      },
      monitoring: {
        name: 'Health Monitoring',
        description: 'Container health checks + Telegram alerts every 5 min',
        status: FEATURE_STATUS.DONE,
        module: 'deploy/monitoring.py',
      },
      backup: {
        name: 'Automated Backup',
        description: 'Daily SQLite backup with 30-day retention, optional S3/rsync',
        status: FEATURE_STATUS.DONE,
        module: 'deploy/backup.sh',
      },
      security: {
        name: 'Security Hardening',
        description: 'fail2ban + UFW firewall + non-root Docker',
        status: FEATURE_STATUS.DONE,
        module: 'deploy/setup.sh',
      },
    },
  },

  // ============================================================
  // PHASE 7: Multi-Asset (Planned)
  // ============================================================
  multi_asset: {
    name: 'Multi-Asset Support',
    status: FEATURE_STATUS.PLANNED,
    features: {
      multi_symbol: {
        name: 'Multi-Symbol Trading',
        description: 'Support ETH, SOL, and other perpetual pairs simultaneously',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      per_symbol_config: {
        name: 'Per-Symbol Configuration',
        description: 'Individual risk and strategy parameters per asset',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      portfolio_correlation: {
        name: 'Portfolio Correlation',
        description: 'Cross-asset correlation risk management',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      symbol_scanner: {
        name: 'Symbol Scanner',
        description: 'Auto-detect high-opportunity trading pairs',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
    },
  },

  // ============================================================
  // PHASE 8: Advanced Strategies (Planned)
  // ============================================================
  advanced_strategies: {
    name: 'Advanced Strategies',
    status: FEATURE_STATUS.PLANNED,
    features: {
      mean_reversion: {
        name: 'Mean Reversion Strategy',
        description: 'Bollinger Band / RSI oversold-overbought entries',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      breakout_strategy: {
        name: 'Breakout Strategy',
        description: 'Support/Resistance level breakout detection',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      order_flow: {
        name: 'Order Flow Analysis',
        description: 'Orderbook depth analysis + liquidation heatmap',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      multi_strategy: {
        name: 'Multi-Strategy Engine',
        description: 'Run multiple strategies per symbol with allocation',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      regime_strategy_switching: {
        name: 'Regime-Based Strategy Switching',
        description: 'Auto-switch strategy based on detected market regime',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
    },
  },

  // ============================================================
  // PHASE 9: Enhanced AI (Planned)
  // ============================================================
  enhanced_ai: {
    name: 'Enhanced AI',
    status: FEATURE_STATUS.PLANNED,
    features: {
      multi_model_ensemble: {
        name: 'Multi-Model Ensemble',
        description: 'Use multiple AI models for consensus-based decisions',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      sentiment_analysis: {
        name: 'Social Sentiment Analysis',
        description: 'Twitter/Reddit/Telegram NLP sentiment scoring',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      pattern_recognition: {
        name: 'Chart Pattern Recognition',
        description: 'CNN/LSTM models for visual chart pattern detection',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      self_learning: {
        name: 'Self-Learning AI',
        description: 'AI learns from its own calibration data to improve over time',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
      market_narrative: {
        name: 'Daily Market Narrative',
        description: 'AI generates daily market thesis and trading plan',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
    },
  },

  // ============================================================
  // PHASE 10: Advanced Risk (Planned)
  // ============================================================
  advanced_risk: {
    name: 'Advanced Risk & Portfolio',
    status: FEATURE_STATUS.PLANNED,
    features: {
      kelly_criterion: {
        name: 'Kelly Criterion Sizing',
        description: 'Optimal position sizing derived from win rate and payoff ratio',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      correlation_risk: {
        name: 'Correlation Risk Management',
        description: 'Reduce total exposure when assets show high correlation',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      drawdown_recovery: {
        name: 'Drawdown Recovery Mode',
        description: 'Progressive position sizing during recovery from drawdowns',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      var_calculation: {
        name: 'Value at Risk (VaR)',
        description: 'Portfolio-level Value at Risk calculation',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
    },
  },

  // ============================================================
  // PHASE 11: User Experience (Planned)
  // ============================================================
  user_experience: {
    name: 'User Experience',
    status: FEATURE_STATUS.PLANNED,
    features: {
      mobile_alerts: {
        name: 'Mobile Push Notifications',
        description: 'Push notifications via mobile app for trade events',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.HIGH,
      },
      trade_replay: {
        name: 'Trade Replay',
        description: 'Visual replay of past trades with chart animation',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      strategy_builder_ui: {
        name: 'No-Code Strategy Builder',
        description: 'Visual strategy configuration without code',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.MEDIUM,
      },
      multi_user: {
        name: 'Multi-User Support',
        description: 'Multiple accounts with separate strategies and configs',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
      rest_api: {
        name: 'REST API',
        description: 'External API endpoints for programmatic access',
        status: FEATURE_STATUS.PLANNED,
        priority: PRIORITY.LOW,
      },
    },
  },
};

// ============================================================
// Installed Claude Code Skills (.claude/skills/)
// Adapted for crypto perpetual futures from tradermonty + SkillsMP
// ============================================================
const installedSkills = {
  auto_invoked: [
    {
      name: 'technical-analyst',
      command: '/technical-analyst',
      description: 'Chart analysis with EMA/RSI/ATR for crypto',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'crypto-expert', 'frontend-dev'],
    },
    {
      name: 'backtest-expert',
      command: '/backtest-expert',
      description: 'Strategy validation + stress testing',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'sa', 'backend-dev'],
    },
    {
      name: 'position-sizer',
      command: '/position-sizer',
      description: 'Position sizing for futures with leverage',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'backend-dev'],
    },
    {
      name: 'macro-regime-detector',
      command: '/macro-regime-detector',
      description: 'Cross-asset macro regime detection for crypto',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'crypto-expert'],
    },
    {
      name: 'market-news-analyst',
      command: '/market-news-analyst',
      description: 'Crypto news impact analysis + scoring',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['crypto-expert'],
    },
    {
      name: 'trader-memory-core',
      command: '/trader-memory-core',
      description: 'Thesis lifecycle tracking (IDEA → CLOSED)',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'pm'],
    },
    {
      name: 'crypto-signal-validator',
      command: '/crypto-signal-validator',
      description: 'Multi-layer signal validation (custom skill)',
      source: 'custom (CCBT project)',
      status: 'installed',
      used_by_agents: ['trader-expert', 'backend-dev'],
    },
  ],
  manual_only: [
    {
      name: 'scenario-analyzer',
      command: '/scenario-analyzer "event"',
      description: '18-month scenario projections from news',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['pm', 'crypto-expert'],
    },
    {
      name: 'strategy-pivot-designer',
      command: '/strategy-pivot-designer',
      description: 'Strategy stagnation diagnosis + pivot proposals',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['trader-expert'],
    },
    {
      name: 'edge-pipeline-orchestrator',
      command: '/edge-pipeline-orchestrator',
      description: 'Full strategy development pipeline',
      source: 'tradermonty/claude-trading-skills (adapted)',
      status: 'installed',
      used_by_agents: ['sa'],
    },
  ],
  not_installed: [
    {
      name: 'Pair Trade Screener',
      source: 'tradermonty/claude-trading-skills',
      reason: 'Statistical arbitrage for future multi-asset phase',
    },
    {
      name: 'Market Top Detector',
      source: 'tradermonty/claude-trading-skills',
      reason: 'Distribution day detection for better entry timing',
    },
    {
      name: 'FTD Detector',
      source: 'tradermonty/claude-trading-skills',
      reason: 'Follow-through day signals for market bottom confirmation',
    },
    {
      name: 'Portfolio Manager',
      source: 'tradermonty/claude-trading-skills',
      reason: 'Portfolio rebalancing with Alpaca integration for multi-asset phase',
    },
  ],
};

// ============================================================
// Agent Team (.claude/agents/)
// 7 specialized agents with team collaboration capabilities
// ============================================================
const agentTeam = {
  strategic: [
    {
      name: 'pm',
      model: 'sonnet',
      role: 'Product Manager',
      responsibilities: 'Roadmap, features, prioritization, user stories',
      skills: ['scenario-analyzer', 'trader-memory-core'],
      memory: 'project',
      communicates_with: ['all'],
    },
    {
      name: 'sa',
      model: 'opus',
      role: 'Solution Architect',
      responsibilities: 'Architecture, technical decisions, code review',
      skills: ['backtest-expert', 'edge-pipeline-orchestrator'],
      memory: 'project',
      communicates_with: ['backend-dev', 'frontend-dev', 'devops', 'pm'],
    },
  ],
  domain_experts: [
    {
      name: 'trader-expert',
      model: 'opus',
      role: 'Trader Expert',
      responsibilities: 'Strategy validation, risk management, backtest analysis',
      skills: [
        'technical-analyst', 'backtest-expert', 'position-sizer',
        'macro-regime-detector', 'strategy-pivot-designer',
        'crypto-signal-validator', 'trader-memory-core',
      ],
      memory: 'project',
      communicates_with: ['pm', 'sa', 'backend-dev', 'crypto-expert'],
    },
    {
      name: 'crypto-expert',
      model: 'opus',
      role: 'Crypto Expert',
      responsibilities: 'Crypto markets, on-chain analysis, exchange mechanics, risk alerts',
      skills: ['macro-regime-detector', 'market-news-analyst', 'scenario-analyzer', 'technical-analyst'],
      memory: 'project',
      communicates_with: ['all (broadcasts risk alerts)'],
    },
  ],
  implementation: [
    {
      name: 'backend-dev',
      model: 'sonnet',
      role: 'Backend Developer',
      responsibilities: 'Python code, trading engine, tests, bug fixes',
      skills: ['crypto-signal-validator', 'position-sizer', 'backtest-expert'],
      memory: 'project',
      owns: ['bot/', 'main.py', 'backtest/', 'tests/'],
      communicates_with: ['sa', 'frontend-dev', 'devops', 'trader-expert'],
    },
    {
      name: 'frontend-dev',
      model: 'sonnet',
      role: 'Frontend Developer',
      responsibilities: 'Streamlit dashboard, Plotly charts, data visualization',
      skills: ['technical-analyst'],
      memory: 'project',
      owns: ['dashboard/'],
      communicates_with: ['sa', 'backend-dev', 'devops'],
    },
    {
      name: 'devops',
      model: 'sonnet',
      role: 'DevOps Engineer',
      responsibilities: 'Docker, deployment, monitoring, security, backups',
      skills: [],
      memory: 'project',
      owns: ['deploy/', 'Dockerfile', 'docker-compose.yml', '.env.example'],
      communicates_with: ['sa', 'backend-dev', 'frontend-dev'],
    },
  ],
};

// ============================================================
// Helper Functions
// ============================================================

/**
 * Get all features with a specific status
 */
function getFeaturesByStatus(status) {
  const results = [];
  for (const [groupKey, group] of Object.entries(features)) {
    for (const [featureKey, feature] of Object.entries(group.features)) {
      if (feature.status === status) {
        results.push({
          group: group.name,
          key: featureKey,
          ...feature,
        });
      }
    }
  }
  return results;
}

/**
 * Get feature count summary
 */
function getFeatureSummary() {
  const summary = { done: 0, in_progress: 0, planned: 0, blocked: 0, total: 0 };
  for (const group of Object.values(features)) {
    for (const feature of Object.values(group.features)) {
      summary[feature.status]++;
      summary.total++;
    }
  }
  return summary;
}

/**
 * Get planned features by priority
 */
function getPlannedByPriority(priority) {
  return getFeaturesByStatus(FEATURE_STATUS.PLANNED)
    .filter(f => f.priority === priority);
}

/**
 * Get all installed skills
 */
function getInstalledSkills() {
  return [
    ...installedSkills.auto_invoked,
    ...installedSkills.manual_only,
  ];
}

/**
 * Get skills used by a specific agent
 */
function getSkillsByAgent(agentName) {
  return getInstalledSkills().filter(s =>
    s.used_by_agents && s.used_by_agents.includes(agentName)
  );
}

/**
 * Get all agents
 */
function getAllAgents() {
  return [
    ...agentTeam.strategic,
    ...agentTeam.domain_experts,
    ...agentTeam.implementation,
  ];
}

module.exports = {
  FEATURE_STATUS,
  PRIORITY,
  features,
  installedSkills,
  agentTeam,
  getFeaturesByStatus,
  getFeatureSummary,
  getPlannedByPriority,
  getInstalledSkills,
  getSkillsByAgent,
  getAllAgents,
};
