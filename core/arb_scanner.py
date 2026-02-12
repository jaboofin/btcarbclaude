"""
╔══════════════════════════════════════════════════════════════════╗
║  ARB SCANNER — Independent Fast-Polling Arbitrage Engine          ║
║                                                                    ║
║  Runs its OWN async loop (every 5-10s), separate from the         ║
║  directional 15-min trading cycle.                                 ║
║                                                                    ║
║  Scans BTC markets across multiple timeframes:                     ║
║    • 15-minute windows                                             ║
║    • 30-minute windows                                             ║
║    • 1-hour windows                                                ║
║                                                                    ║
║  When YES + NO < threshold → buys both sides instantly.            ║
║  No predictions involved — pure pricing gap capture.               ║
║                                                                    ║
║  Activated via: python bot.py --arb                                ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger("arb_scanner")


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class ArbMarket:
    """A discovered market with both YES/NO sides."""
    condition_id: str
    question: str
    slug: str
    token_id_yes: str
    token_id_no: str
    price_yes: float
    price_no: float
    liquidity: float
    end_date: str
    timeframe: str          # "15m", "30m", "1h"

    @property
    def combined(self) -> float:
        return self.price_yes + self.price_no

    @property
    def edge_pct(self) -> float:
        return (1.0 - self.combined) * 100

    @property
    def is_arb(self) -> bool:
        return self.combined < 1.0


@dataclass
class ArbExecution:
    """Record of an executed arb trade."""
    timestamp: float
    condition_id: str
    question: str
    timeframe: str
    price_yes: float
    price_no: float
    combined: float
    edge_pct: float
    size_per_side: float
    guaranteed_profit: float
    order_id_yes: Optional[str] = None
    order_id_no: Optional[str] = None
    status: str = "pending"       # pending, filled, failed


@dataclass
class ArbScannerConfig:
    """Configuration for the arb scanner."""
    poll_interval_secs: float = 8.0     # How often to scan (seconds)
    arb_threshold: float = 0.98         # Buy both if YES+NO < this
    min_edge_pct: float = 1.0           # Skip tiny edges below 1%
    size_per_side_usd: float = 10.0     # USD to buy each side
    max_daily_arb_trades: int = 50      # Daily limit on arb trade pairs
    max_daily_arb_budget: float = 200.0 # Max USD committed to arb per day
    min_liquidity_usd: float = 25.0     # Skip illiquid markets
    cooldown_per_market_secs: float = 120.0  # Don't re-arb same market within 2min
    scan_timeframes: list = field(default_factory=lambda: ["15m", "30m", "1h"])


# ── Market Timeframe Patterns ────────────────────────────────────

TIMEFRAME_PATTERNS = {
    "15m": {
        "keywords": ["15-min", "15 min", "15min", "15-minute"],
        "label": "15-Minute",
    },
    "30m": {
        "keywords": ["30-min", "30 min", "30min", "30-minute"],
        "label": "30-Minute",
    },
    "1h": {
        "keywords": ["1-hour", "1 hour", "1hour", "60-min", "60 min", "hourly"],
        "label": "1-Hour",
    },
}


class ArbScanner:
    """
    Independent arbitrage scanner.

    Runs a fast async loop separate from the directional trading cycle.
    Discovers BTC markets across 15m / 30m / 1h timeframes and
    instantly captures any YES+NO mispricing.

    Usage:
        scanner = ArbScanner(config, polymarket_client)
        asyncio.create_task(scanner.run())   # Fire and forget
        ...
        scanner.stop()
    """

    def __init__(self, config: ArbScannerConfig, polymarket_client=None):
        self.config = config
        self.polymarket = polymarket_client
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

        # State
        self._known_markets: dict[str, ArbMarket] = {}
        self._executions: list[ArbExecution] = []
        self._cooldowns: dict[str, float] = {}  # condition_id → last arb timestamp
        self._daily_trades = 0
        self._daily_spent = 0.0
        self._daily_profit = 0.0
        self._day_start = 0.0
        self._scan_count = 0
        self._last_discovery = 0.0

        # Discovery cache (don't hit Gamma API every 8 seconds)
        self._discovery_interval = 60.0  # Re-discover markets every 60s
        self._gamma_url = "https://gamma-api.polymarket.com"

    # ── HTTP Session ─────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def _close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Market Discovery ─────────────────────────────────────────

    def _classify_timeframe(self, text: str) -> Optional[str]:
        """Determine which timeframe a market belongs to."""
        text_lower = text.lower()
        for tf, patterns in TIMEFRAME_PATTERNS.items():
            if tf not in self.config.scan_timeframes:
                continue
            if any(kw in text_lower for kw in patterns["keywords"]):
                return tf
        return None

    async def _discover_markets(self) -> list[ArbMarket]:
        """
        Fetch active BTC binary markets from Gamma API.
        Classifies each into 15m / 30m / 1h based on question text.
        """
        now = time.time()
        if now - self._last_discovery < self._discovery_interval and self._known_markets:
            return list(self._known_markets.values())

        try:
            session = await self._get_session()
            url = f"{self._gamma_url}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
                "order": "endDate",
                "ascending": "true",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"Gamma API returned {resp.status}")
                    return list(self._known_markets.values())
                data = await resp.json()

            markets = []
            for m in data:
                combined_text = f"{m.get('question', '')} {m.get('slug', '')} {m.get('description', '')}".lower()

                # Must be BTC
                is_btc = any(k in combined_text for k in ["btc", "bitcoin"])
                if not is_btc:
                    continue

                # Must be directional (up/down)
                is_dir = any(k in combined_text for k in ["up or down", "above", "below", "higher", "lower"])
                if not is_dir:
                    continue

                # Classify timeframe
                tf = self._classify_timeframe(combined_text)
                if not tf:
                    continue

                tokens = m.get("tokens", [])
                if len(tokens) < 2:
                    continue

                liquidity = float(m.get("liquidityClob", 0))
                if liquidity < self.config.min_liquidity_usd:
                    continue

                market = ArbMarket(
                    condition_id=m.get("conditionId", m.get("id", "")),
                    question=m.get("question", ""),
                    slug=m.get("slug", ""),
                    token_id_yes=tokens[0].get("token_id", ""),
                    token_id_no=tokens[1].get("token_id", ""),
                    price_yes=float(tokens[0].get("price", 0.5)),
                    price_no=float(tokens[1].get("price", 0.5)),
                    liquidity=liquidity,
                    end_date=m.get("endDate", ""),
                    timeframe=tf,
                )
                markets.append(market)
                self._known_markets[market.condition_id] = market

            self._last_discovery = now

            # Log discovery summary
            by_tf = {}
            for mkt in markets:
                by_tf.setdefault(mkt.timeframe, 0)
                by_tf[mkt.timeframe] += 1
            summary = " · ".join(f"{TIMEFRAME_PATTERNS[k]['label']}: {v}" for k, v in sorted(by_tf.items()))
            logger.info(f"🔍 Discovered {len(markets)} BTC markets — {summary}")

            return markets

        except Exception as e:
            logger.error(f"Discovery error: {e}")
            return list(self._known_markets.values())

    # ── Price Refresh ────────────────────────────────────────────

    async def _refresh_prices(self, markets: list[ArbMarket]) -> list[ArbMarket]:
        """
        Refresh YES/NO prices from Gamma API for known markets.
        This is the fast-path — lighter than full discovery.
        """
        if not markets:
            return markets

        # For now, prices come from discovery. In production, you'd
        # hit the CLOB orderbook for real-time best bid/ask.
        # The Gamma API prices update frequently enough for arb scanning.
        try:
            session = await self._get_session()
            # Batch fetch active markets
            condition_ids = [m.condition_id for m in markets[:20]]
            for cid in condition_ids:
                url = f"{self._gamma_url}/markets/{cid}"
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            tokens = data.get("tokens", [])
                            if len(tokens) >= 2 and cid in self._known_markets:
                                self._known_markets[cid].price_yes = float(tokens[0].get("price", 0.5))
                                self._known_markets[cid].price_no = float(tokens[1].get("price", 0.5))
                except Exception:
                    pass  # Don't let one failed refresh kill the loop

            return list(self._known_markets.values())
        except Exception as e:
            logger.error(f"Price refresh error: {e}")
            return markets

    # ── Arb Detection ────────────────────────────────────────────

    def _find_opportunities(self, markets: list[ArbMarket]) -> list[ArbMarket]:
        """Find markets where YES + NO < threshold."""
        now = time.time()
        opps = []

        for m in markets:
            # Skip if combined price is above threshold
            if m.combined >= self.config.arb_threshold:
                continue

            # Skip if edge is too small
            if m.edge_pct < self.config.min_edge_pct:
                continue

            # Skip if on cooldown
            last_arb = self._cooldowns.get(m.condition_id, 0)
            if now - last_arb < self.config.cooldown_per_market_secs:
                continue

            opps.append(m)

        return opps

    # ── Execution ────────────────────────────────────────────────

    async def _execute_arb(self, market: ArbMarket) -> Optional[ArbExecution]:
        """Buy both YES and NO sides of a mispriced market."""
        now = time.time()

        # Daily limits
        if self._daily_trades >= self.config.max_daily_arb_trades:
            logger.info("Daily arb trade limit reached")
            return None
        cost = self.config.size_per_side_usd * 2
        if self._daily_spent + cost > self.config.max_daily_arb_budget:
            logger.info(f"Daily arb budget limit (${self.config.max_daily_arb_budget:.0f})")
            return None

        profit = self.config.size_per_side_usd * (1.0 / market.combined - 1.0)

        execution = ArbExecution(
            timestamp=now,
            condition_id=market.condition_id,
            question=market.question,
            timeframe=market.timeframe,
            price_yes=market.price_yes,
            price_no=market.price_no,
            combined=market.combined,
            edge_pct=market.edge_pct,
            size_per_side=self.config.size_per_side_usd,
            guaranteed_profit=round(profit, 2),
        )

        logger.info(
            f"💰 ARB [{market.timeframe.upper()}]: {market.question[:60]}... | "
            f"YES={market.price_yes:.3f} + NO={market.price_no:.3f} = {market.combined:.3f} | "
            f"edge={market.edge_pct:.1f}% | profit=${profit:.2f}"
        )

        # Execute via polymarket client if available
        if self.polymarket:
            try:
                # Reconstruct a BinaryMarket for the client
                from core.polymarket_client import BinaryMarket, MarketStatus
                bm = BinaryMarket(
                    condition_id=market.condition_id,
                    question=market.question,
                    slug=market.slug,
                    token_id_up=market.token_id_yes,
                    token_id_down=market.token_id_no,
                    price_up=market.price_yes,
                    price_down=market.price_no,
                    volume=0, liquidity=market.liquidity,
                    created_at="", end_date=market.end_date,
                    status=MarketStatus.ACTIVE,
                )

                # Buy YES
                yes_trade = await self.polymarket.place_order(
                    market=bm, direction="up",
                    size_usd=self.config.size_per_side_usd,
                    oracle_price=0.0, confidence=1.0,
                )
                if yes_trade:
                    execution.order_id_yes = yes_trade.order_id

                # Buy NO
                no_trade = await self.polymarket.place_order(
                    market=bm, direction="down",
                    size_usd=self.config.size_per_side_usd,
                    oracle_price=0.0, confidence=1.0,
                )
                if no_trade:
                    execution.order_id_no = no_trade.order_id

                if yes_trade and no_trade:
                    execution.status = "filled"
                    logger.info(f"✅ ARB FILLED: ${profit:.2f} locked profit")
                elif yes_trade or no_trade:
                    execution.status = "partial"
                    logger.warning(f"⚠️ ARB PARTIAL: only one side filled")
                else:
                    execution.status = "failed"
                    logger.error(f"❌ ARB FAILED: neither side filled")

            except Exception as e:
                execution.status = "failed"
                logger.error(f"Arb execution error: {e}")
        else:
            # Dry run mode (no polymarket client)
            execution.status = "dry_run"
            logger.info(f"🏜️ DRY RUN — would lock ${profit:.2f}")

        # Update tracking
        self._executions.append(execution)
        self._cooldowns[market.condition_id] = now
        self._daily_trades += 1
        self._daily_spent += cost
        if execution.status in ("filled", "dry_run"):
            self._daily_profit += profit

        return execution

    # ── Daily Reset ──────────────────────────────────────────────

    def _check_daily_reset(self):
        """Reset daily counters at midnight UTC."""
        now = time.time()
        if now - self._day_start > 86400:
            prev_trades = self._daily_trades
            prev_profit = self._daily_profit
            self._daily_trades = 0
            self._daily_spent = 0.0
            self._daily_profit = 0.0
            self._day_start = now
            if prev_trades > 0:
                logger.info(
                    f"📊 Daily reset — yesterday: {prev_trades} arb trades, "
                    f"${prev_profit:.2f} profit"
                )

    # ── Main Loop ────────────────────────────────────────────────

    async def run(self):
        """
        Main arb scanner loop. Runs independently of the trading bot.
        Call as: asyncio.create_task(scanner.run())
        """
        self._running = True
        self._day_start = time.time()

        logger.info(
            f"🚀 Arb scanner started — "
            f"polling every {self.config.poll_interval_secs}s | "
            f"timeframes: {', '.join(self.config.scan_timeframes)} | "
            f"threshold: {self.config.arb_threshold} | "
            f"budget: ${self.config.max_daily_arb_budget}/day"
        )

        while self._running:
            try:
                self._check_daily_reset()
                self._scan_count += 1

                # Discover or refresh markets
                if time.time() - self._last_discovery > self._discovery_interval:
                    markets = await self._discover_markets()
                else:
                    markets = await self._refresh_prices(list(self._known_markets.values()))

                # Find opportunities
                opps = self._find_opportunities(markets)

                if opps:
                    # Sort by edge (best first)
                    opps.sort(key=lambda m: m.edge_pct, reverse=True)

                    for opp in opps:
                        await self._execute_arb(opp)

                        # Check limits after each execution
                        if self._daily_trades >= self.config.max_daily_arb_trades:
                            break
                        if self._daily_spent >= self.config.max_daily_arb_budget:
                            break

                # Periodic status log (every ~30 scans = ~4 minutes)
                if self._scan_count % 30 == 0:
                    logger.info(
                        f"📡 Arb scan #{self._scan_count} | "
                        f"{len(self._known_markets)} markets tracked | "
                        f"today: {self._daily_trades} trades, "
                        f"${self._daily_profit:.2f} profit, "
                        f"${self._daily_spent:.2f} committed"
                    )

            except Exception as e:
                logger.error(f"Arb scan error: {e}", exc_info=True)

            await asyncio.sleep(self.config.poll_interval_secs)

        await self._close_session()
        logger.info("Arb scanner stopped")

    def stop(self):
        """Stop the arb scanner loop."""
        self._running = False

    # ── Stats / Dashboard ────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return current arb scanner stats for dashboard / logging."""
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "markets_tracked": len(self._known_markets),
            "markets_by_timeframe": self._count_by_timeframe(),
            "daily_trades": self._daily_trades,
            "daily_profit": round(self._daily_profit, 2),
            "daily_spent": round(self._daily_spent, 2),
            "daily_budget_remaining": round(self.config.max_daily_arb_budget - self._daily_spent, 2),
            "total_executions": len(self._executions),
            "recent_arbs": [
                {
                    "time": e.timestamp,
                    "timeframe": e.timeframe,
                    "edge_pct": e.edge_pct,
                    "profit": e.guaranteed_profit,
                    "status": e.status,
                    "question": e.question[:60],
                }
                for e in self._executions[-10:]
            ],
        }

    def _count_by_timeframe(self) -> dict:
        counts = {}
        for m in self._known_markets.values():
            counts.setdefault(m.timeframe, 0)
            counts[m.timeframe] += 1
        return counts

    def get_executions(self) -> list[ArbExecution]:
        return self._executions.copy()
