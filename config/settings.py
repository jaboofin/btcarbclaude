"""
╔══════════════════════════════════════════════════════════════════════╗
║  BTC-15M-Oracle — CONFIGURATION                                     ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
from dataclasses import dataclass, field
from enum import Enum


class MarketDirection(Enum):
    UP = "up"
    DOWN = "down"
    HOLD = "hold"


@dataclass
class OracleConfig:
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    binance_base_url: str = "https://api.binance.com/api/v3"
    binance_ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"
    coincap_base_url: str = "https://api.coincap.io/v2"
    poll_interval: int = 10
    max_price_age: int = 30
    min_oracle_consensus: int = 2
    history_candle_count: int = 100
    candle_interval: str = "15m"


@dataclass
class PolymarketConfig:
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    rpc_url: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    private_key: str = os.getenv("POLY_PRIVATE_KEY", "")
    funder: str = os.getenv("POLY_FUNDER", "")
    sig_type: int = int(os.getenv("POLY_SIG_TYPE", "0"))
    market_slug_pattern: str = "btc-price"
    market_interval_minutes: int = 15
    order_type: str = "market"
    max_slippage_pct: float = 2.0
    min_liquidity_usd: float = 50.0


@dataclass
class StrategyConfig:
    confidence_threshold: float = 0.60
    strong_signal_threshold: float = 0.75
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    ema_fast: int = 5
    ema_slow: int = 15
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    momentum_lookback: int = 3
    min_volatility_pct: float = 0.05
    max_volatility_pct: float = 3.0
    weight_momentum: float = 0.30
    weight_rsi: float = 0.25
    weight_macd: float = 0.25
    weight_ema_cross: float = 0.20


@dataclass
class RiskConfig:
    max_trade_pct: float = 5.0
    max_daily_trades: int = 20
    max_daily_loss_pct: float = 15.0
    max_consecutive_losses: int = 5
    loss_streak_cooldown_mins: int = 60
    kelly_fraction: float = 0.25
    min_trade_size_usd: float = 1.0
    max_trade_size_usd: float = 25.0


@dataclass
class EdgeConfig:
    """Arbitrage + Hedge toggles."""
    # ── Arbitrage (independent scanner) ──
    enable_arb: bool = False             # --arb to turn on
    arb_threshold: float = 0.98          # buy both if YES+NO < this
    arb_min_edge_pct: float = 1.0        # skip tiny edges below 1%
    arb_size_usd: float = 10.0           # USD per side on arb trades
    arb_poll_secs: float = 8.0           # scan every N seconds
    arb_max_daily_trades: int = 50       # daily arb trade pair limit
    arb_max_daily_budget: float = 200.0  # max USD committed per day
    arb_cooldown_secs: float = 120.0     # don't re-arb same market within 2min
    arb_timeframes: list = field(default_factory=lambda: ["15m", "30m", "1h"])
    # ── Hedge ──
    enable_hedge: bool = False           # --hedge to turn on
    hedge_min_confidence: float = 0.65   # only hedge if flip signal is strong


@dataclass
class LoggingConfig:
    log_dir: str = "logs"
    trade_log_file: str = "logs/trades.jsonl"
    strategy_log_file: str = "logs/strategy.jsonl"
    oracle_log_file: str = "logs/oracle.jsonl"
    error_log_file: str = "logs/errors.log"
    performance_file: str = "data/performance.json"
    alert_on_loss_streak: int = 3
    alert_on_oracle_downtime_secs: int = 60


@dataclass
class BotConfig:
    oracle: OracleConfig = field(default_factory=OracleConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    edge: EdgeConfig = field(default_factory=EdgeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    bot_name: str = "BTC-15M-Oracle"
    version: str = "2.0.0"
    # Bankroll — set via CLI: python bot.py --bankroll 500
    bankroll: float = 500.0
    # Clock-sync timing
    entry_lead_secs: int = 60
    entry_window_secs: int = 30
    sleep_poll_secs: int = 5
