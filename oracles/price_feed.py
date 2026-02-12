"""
╔══════════════════════════════════════════════════════════════════╗
║  ORACLE ENGINE — Chainlink-First BTC Price Feed                  ║
║                                                                    ║
║  Polymarket 15-min markets resolve against Chainlink BTC/USD.    ║
║  This engine:                                                      ║
║    1. Fetches Chainlink price via Polymarket RTDS websocket       ║
║    2. Falls back to Binance + CoinGecko for redundancy            ║
║    3. Tracks the OPENING PRICE of each 15-min window              ║
║    4. Provides candles from Binance for technical analysis         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import time
import json
import logging
import datetime
from dataclasses import dataclass, field
from typing import Optional
from statistics import median

import aiohttp

logger = logging.getLogger("oracle")


@dataclass
class PricePoint:
    source: str
    price: float
    timestamp: float
    volume_24h: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def is_stale(self, max_age: int = 30) -> bool:
        return self.age_seconds > max_age


@dataclass
class ConsensusPrice:
    price: float
    timestamp: float
    sources: list
    spread_pct: float
    confidence: float
    chainlink_price: Optional[float] = None  # The actual resolution oracle

    def __repr__(self):
        src = ", ".join(self.sources)
        cl = f" CL=${self.chainlink_price:,.2f}" if self.chainlink_price else ""
        return f"${self.price:,.2f} | spread={self.spread_pct:.3f}% | [{src}]{cl}"


@dataclass
class WindowAnchor:
    """Tracks the opening price of the current 15-min window."""
    boundary_time: float         # Unix ts of boundary start (e.g. 12:00:00)
    open_price: float            # Chainlink BTC/USD at boundary start
    source: str                  # "chainlink" or "binance" (fallback)
    captured_at: float           # When we recorded it

    @property
    def age_seconds(self) -> float:
        return time.time() - self.captured_at

    def price_vs_open(self, current: float) -> float:
        """Current price relative to open, as percentage."""
        if self.open_price <= 0:
            return 0.0
        return ((current - self.open_price) / self.open_price) * 100


@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    interval: str = "15m"


class OracleEngine:
    """
    Chainlink-first BTC price oracle with window anchor tracking.

    Priority:
      1. Chainlink BTC/USD (via Polymarket RTDS or data.chain.link)
      2. Binance BTCUSDT (fast, reliable, tracks Chainlink closely)
      3. CoinGecko BTC/USD (fallback)

    Also tracks the opening price of each 15-minute window,
    since Polymarket resolves: close >= open → UP, else DOWN.
    """

    MAX_DIVERGENCE_PCT = 1.0
    RTDS_URL = "wss://ws-live-data.polymarket.com"

    def __init__(self, config):
        self.config = config.oracle
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_prices: dict[str, PricePoint] = {}
        self._price_history: list[ConsensusPrice] = []
        self._chainlink_price: Optional[float] = None
        self._chainlink_ts: float = 0
        self._window_anchor: Optional[WindowAnchor] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Chainlink via Polymarket RTDS ────────────────────────────

    async def _fetch_chainlink_rtds(self) -> Optional[PricePoint]:
        """
        Connect to Polymarket RTDS websocket, subscribe to
        crypto_prices_chainlink, grab one BTC/USD price, disconnect.
        """
        try:
            session = await self._get_session()
            async with session.ws_connect(self.RTDS_URL, timeout=8) as ws:
                # Subscribe to Chainlink BTC/USD
                await ws.send_json({
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": '{"symbol":"btc/usd"}',
                    }]
                })

                # Wait for one price update (timeout 6s)
                start = time.time()
                while time.time() - start < 6:
                    msg = await asyncio.wait_for(ws.receive(), timeout=6)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("topic") == "crypto_prices_chainlink":
                            payload = data.get("payload", {})
                            if payload.get("symbol") == "btc/usd" and "value" in payload:
                                price = float(payload["value"])
                                ts = payload.get("timestamp", time.time() * 1000) / 1000
                                self._chainlink_price = price
                                self._chainlink_ts = ts
                                logger.info(f"Chainlink BTC/USD: ${price:,.2f}")
                                return PricePoint(
                                    source="chainlink",
                                    price=price,
                                    timestamp=ts,
                                )
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

                logger.warning("Chainlink RTDS: no price received within timeout")
                return None
        except Exception as e:
            logger.warning(f"Chainlink RTDS failed: {e}")
            return None

    # ── Binance ──────────────────────────────────────────────────

    async def _fetch_binance(self) -> Optional[PricePoint]:
        try:
            session = await self._get_session()
            url = f"{self.config.binance_base_url}/ticker/bookTicker"
            async with session.get(url, params={"symbol": "BTCUSDT"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bid, ask = float(data["bidPrice"]), float(data["askPrice"])
                    return PricePoint(source="binance", price=(bid + ask) / 2, timestamp=time.time(), bid=bid, ask=ask)
                logger.warning(f"Binance {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Binance: {e}")
            return None

    # ── CoinGecko ────────────────────────────────────────────────

    async def _fetch_coingecko(self) -> Optional[PricePoint]:
        try:
            session = await self._get_session()
            url = f"{self.config.coingecko_base_url}/simple/price"
            async with session.get(url, params={"ids": "bitcoin", "vs_currencies": "usd"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return PricePoint(source="coingecko", price=data["bitcoin"]["usd"], timestamp=time.time())
                logger.warning(f"CoinGecko {resp.status}")
                return None
        except Exception as e:
            logger.error(f"CoinGecko: {e}")
            return None

    # ── Consensus ────────────────────────────────────────────────

    async def get_price(self) -> ConsensusPrice:
        """
        Fetch BTC price. Chainlink is primary (resolution oracle).
        Binance + CoinGecko provide redundancy and divergence checks.
        """
        results = await asyncio.gather(
            self._fetch_chainlink_rtds(),
            self._fetch_binance(),
            self._fetch_coingecko(),
            return_exceptions=True,
        )

        valid: list[PricePoint] = []
        chainlink_pp = None
        for r in results:
            if isinstance(r, PricePoint) and r is not None:
                if not r.is_stale(self.config.max_price_age):
                    valid.append(r)
                    self._last_prices[r.source] = r
                    if r.source == "chainlink":
                        chainlink_pp = r

        # Fallback to cache
        if len(valid) < self.config.min_oracle_consensus:
            for src, pp in self._last_prices.items():
                if not pp.is_stale(60) and pp not in valid:
                    valid.append(pp)
                    logger.warning(f"Using cached {src} (age: {pp.age_seconds:.0f}s)")

        if not valid:
            raise RuntimeError("ALL ORACLES DOWN")

        # Price selection: prefer Chainlink, then median
        prices = [pp.price for pp in valid]
        if chainlink_pp:
            price = chainlink_pp.price
        else:
            price = median(prices)

        spread_pct = ((max(prices) - min(prices)) / price) * 100 if len(prices) > 1 else 0.0
        if spread_pct > self.MAX_DIVERGENCE_PCT:
            logger.error(f"Divergence {spread_pct:.3f}%: {', '.join(f'{p.source}=${p.price:,.2f}' for p in valid)}")
            confidence = max(0.2, 1.0 - spread_pct / 5.0)
        else:
            confidence = min(1.0, len(valid) / 3.0)

        consensus = ConsensusPrice(
            price=price,
            timestamp=time.time(),
            sources=[pp.source for pp in valid],
            spread_pct=spread_pct,
            confidence=confidence,
            chainlink_price=chainlink_pp.price if chainlink_pp else None,
        )
        self._price_history.append(consensus)
        logger.info(f"Oracle: {consensus}")
        return consensus

    # ── Window Anchor ────────────────────────────────────────────

    def _current_window_boundary(self) -> float:
        """Start of the CURRENT 15-min window (the one we're inside)."""
        now = time.time()
        dt = datetime.datetime.fromtimestamp(now)
        window_start_min = (dt.minute // 15) * 15
        boundary = dt.replace(minute=window_start_min, second=0, microsecond=0)
        return boundary.timestamp()

    async def capture_window_open(self) -> WindowAnchor:
        """
        Capture the opening price of the current 15-min window.
        This is the price Polymarket uses as the reference —
        end_price >= open_price → UP wins.

        Should be called right at or just after the boundary.
        """
        boundary_ts = self._current_window_boundary()

        # If we already have an anchor for this window, return it
        if self._window_anchor and self._window_anchor.boundary_time == boundary_ts:
            return self._window_anchor

        # Fetch fresh price — Chainlink preferred
        consensus = await self.get_price()

        source = "chainlink" if consensus.chainlink_price else consensus.sources[0]
        open_price = consensus.chainlink_price or consensus.price

        self._window_anchor = WindowAnchor(
            boundary_time=boundary_ts,
            open_price=open_price,
            source=source,
            captured_at=time.time(),
        )

        boundary_dt = datetime.datetime.fromtimestamp(boundary_ts)
        logger.info(
            f"📌 Window anchor: ${open_price:,.2f} ({source}) "
            f"for {boundary_dt.strftime('%H:%M')} window"
        )
        return self._window_anchor

    def get_window_anchor(self) -> Optional[WindowAnchor]:
        """Return the current window's opening price anchor."""
        return self._window_anchor

    # ── Candles ──────────────────────────────────────────────────

    async def get_candles(self, interval: str = "15m", limit: int = 100) -> list[Candle]:
        """Fetch historical candles from Binance (best candle source)."""
        try:
            session = await self._get_session()
            url = f"{self.config.binance_base_url}/klines"
            params = {"symbol": "BTCUSDT", "interval": interval, "limit": min(limit, 1000)}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Binance klines {resp.status}")
                data = await resp.json()
            return [
                Candle(
                    timestamp=k[0] / 1000, open=float(k[1]),
                    high=float(k[2]), low=float(k[3]),
                    close=float(k[4]), volume=float(k[5]),
                    interval=interval,
                )
                for k in data
            ]
        except Exception as e:
            logger.error(f"Candles: {e}")
            return []

    def get_price_history(self) -> list[ConsensusPrice]:
        return self._price_history.copy()
