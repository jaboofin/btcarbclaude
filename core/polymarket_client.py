"""
╔══════════════════════════════════════════════════════════════════╗
║  POLYMARKET CLOB CLIENT — LIVE TRADING                           ║
║  py-clob-client SDK · EIP-712 signing · FOK/GTC orders           ║
║  BTC 15-min UP/DOWN only                                         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import logging
import json
import os
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import aiohttp

logger = logging.getLogger("polymarket")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        OrderArgs, MarketOrderArgs, OrderType, BookParams, OpenOrderParams,
    )
    from py_clob_client.order_builder.constants import BUY, SELL
    HAS_CLOB_SDK = True
except ImportError:
    HAS_CLOB_SDK = False
    logger.warning("py-clob-client not installed. Run: pip install py-clob-client")


class MarketStatus(Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"


@dataclass
class BinaryMarket:
    condition_id: str
    question: str
    slug: str
    token_id_up: str
    token_id_down: str
    price_up: float
    price_down: float
    volume: float
    liquidity: float
    created_at: str
    end_date: str
    status: MarketStatus
    resolved: bool = False
    resolution: Optional[str] = None

    @property
    def is_tradeable(self) -> bool:
        return self.status == MarketStatus.ACTIVE and not self.resolved

    @property
    def spread(self) -> float:
        return abs(1.0 - self.price_up - self.price_down)


@dataclass
class TradeRecord:
    trade_id: str
    timestamp: float
    market_condition_id: str
    direction: str
    confidence: float
    entry_price: float
    size_usd: float
    oracle_price_at_entry: float
    outcome: Optional[str] = None
    pnl: float = 0.0
    order_id: Optional[str] = None
    tx_hashes: list = field(default_factory=list)


class PolymarketClient:
    """Live Polymarket CLOB client using py-clob-client SDK."""

    def __init__(self, config):
        self.config = config.polymarket
        self._session: Optional[aiohttp.ClientSession] = None
        self._clob: Optional[object] = None
        self._clob_initialized = False
        self._active_markets: dict[str, BinaryMarket] = {}
        self._trade_records: list[TradeRecord] = []

    # ── CLOB Init ───────────────────────────────────────────────

    def _init_clob_client(self):
        if self._clob_initialized:
            return
        if not HAS_CLOB_SDK:
            raise RuntimeError("pip install py-clob-client")

        pk = self.config.private_key or os.getenv("POLY_PRIVATE_KEY", "")
        funder = os.getenv("POLY_FUNDER", "")
        sig = int(os.getenv("POLY_SIG_TYPE", "0"))

        if not pk:
            raise RuntimeError("Set POLY_PRIVATE_KEY (export from reveal.polymarket.com)")

        if sig == 0:
            self._clob = ClobClient(self.config.clob_api_url, key=pk, chain_id=self.config.chain_id)
        elif sig in (1, 2):
            if not funder:
                raise RuntimeError("Set POLY_FUNDER (your Polymarket deposit address)")
            self._clob = ClobClient(self.config.clob_api_url, key=pk, chain_id=self.config.chain_id, signature_type=sig, funder=funder)
        else:
            raise ValueError(f"Invalid POLY_SIG_TYPE: {sig}")

        self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
        self._clob_initialized = True
        logger.info(f"CLOB ready (sig_type={sig})")

    # ── HTTP ────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers={"Content-Type": "application/json"})
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Market Discovery ────────────────────────────────────────

    async def discover_markets(self) -> list[BinaryMarket]:
        try:
            session = await self._get_session()
            url = f"{self.config.gamma_api_url}/markets"
            params = {"active": "true", "closed": "false", "limit": 50, "order": "endDate", "ascending": "true"}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Gamma API {resp.status}")
                    return []
                data = await resp.json()

            markets = []
            for m in data:
                combined = f"{m.get('question', '')} {m.get('slug', '')} {m.get('description', '')}".lower()
                is_btc = any(k in combined for k in ["btc", "bitcoin"])
                is_15m = any(k in combined for k in ["15-min", "15 min", "15min", "15-minute"])
                is_dir = any(k in combined for k in ["up or down", "above", "below", "higher", "lower"])
                if is_btc and (is_15m or is_dir):
                    tokens = m.get("tokens", [])
                    if len(tokens) >= 2:
                        market = BinaryMarket(
                            condition_id=m.get("conditionId", m.get("id", "")), question=m.get("question", ""),
                            slug=m.get("slug", ""), token_id_up=tokens[0].get("token_id", ""),
                            token_id_down=tokens[1].get("token_id", ""), price_up=float(tokens[0].get("price", 0.5)),
                            price_down=float(tokens[1].get("price", 0.5)), volume=float(m.get("volume", 0)),
                            liquidity=float(m.get("liquidityClob", 0)), created_at=m.get("createdAt", ""),
                            end_date=m.get("endDate", ""), status=MarketStatus.ACTIVE,
                        )
                        markets.append(market)
                        self._active_markets[market.condition_id] = market
            logger.info(f"Found {len(markets)} BTC 15-min markets")
            return markets
        except Exception as e:
            logger.error(f"Discovery failed: {e}")
            return []

    # ── CLOB Price ──────────────────────────────────────────────

    def get_clob_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        if not self._clob_initialized:
            return None
        try:
            p = self._clob.get_price(token_id, side=side)
            return float(p) if p else None
        except Exception as e:
            logger.error(f"CLOB price: {e}")
            return None

    # ── Order Execution ─────────────────────────────────────────

    async def place_order(self, market: BinaryMarket, direction: str, size_usd: float,
                          price: Optional[float] = None, oracle_price: float = 0.0,
                          confidence: float = 0.0) -> Optional[TradeRecord]:
        token_id = market.token_id_up if direction == "up" else market.token_id_down
        market_price = market.price_up if direction == "up" else market.price_down
        if price is None:
            price = market_price
        if size_usd < 0.50:
            logger.warning(f"Size ${size_usd:.2f} too small")
            return None

        trade_id = f"T-{int(time.time() * 1000)}-{direction[0].upper()}"

        if not self._clob_initialized:
            self._init_clob_client()

        try:
            clob_price = self.get_clob_price(token_id, side="BUY")
            exec_price = clob_price if clob_price else price
            logger.info(f"Price: {exec_price:.4f} (clob={clob_price}, gamma={price:.4f})")

            if exec_price < 0.01 or exec_price > 0.99:
                logger.error(f"Price {exec_price} out of bounds")
                return None

            shares = round(size_usd / exec_price, 2)
            if shares < 1:
                shares = 1.0

            mode = self.config.order_type.lower()

            if mode == "market":
                logger.info(f"🔴 MARKET ORDER: {direction.upper()} ${size_usd:.2f} ({shares:.1f} shares)")
                args = MarketOrderArgs(token_id=token_id, amount=size_usd, side=BUY, order_type=OrderType.FOK)
                signed = self._clob.create_market_order(args)
                resp = self._clob.post_order(signed, OrderType.FOK)
            else:
                logger.info(f"🔴 LIMIT ORDER: {direction.upper()} {shares:.1f} @ {exec_price:.4f}")
                args = OrderArgs(price=exec_price, size=shares, side=BUY, token_id=token_id)
                signed = self._clob.create_order(args)
                resp = self._clob.post_order(signed, OrderType.GTC)

            logger.info(f"Response: {json.dumps(resp, indent=2)}")

            order_id = resp.get("orderID", trade_id)
            success = resp.get("success", False)
            status = resp.get("status", "unknown")
            tx_hashes = resp.get("transactionsHashes", [])

            if not success and status not in ("matched", "live"):
                logger.error(f"FAILED: {resp.get('errorMsg', 'unknown')} ({status})")
                return None

            taking = float(resp.get("takingAmount", 0))
            making = float(resp.get("makingAmount", 0))
            fill_price = (taking / making) if (making > 0 and taking > 0) else exec_price

            record = TradeRecord(
                trade_id=trade_id, timestamp=time.time(), market_condition_id=market.condition_id,
                direction=direction, confidence=confidence, entry_price=fill_price,
                size_usd=size_usd, oracle_price_at_entry=oracle_price,
                order_id=order_id, tx_hashes=tx_hashes,
            )
            self._trade_records.append(record)
            logger.info(f"✅ {trade_id} | {direction.upper()} | ${size_usd:.2f} @ {fill_price:.4f} | {status}")
            return record

        except Exception as e:
            logger.error(f"Trade FAILED: {e}", exc_info=True)
            return None

    # ── Order Management ────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if not self._clob_initialized: self._init_clob_client()
        try: self._clob.cancel(order_id); return True
        except Exception as e: logger.error(f"Cancel: {e}"); return False

    def cancel_all_orders(self) -> bool:
        if not self._clob_initialized: self._init_clob_client()
        try: self._clob.cancel_all(); return True
        except Exception as e: logger.error(f"Cancel all: {e}"); return False

    # ── Resolution ──────────────────────────────────────────────

    async def check_resolutions(self) -> list[TradeRecord]:
        resolved = []
        for r in self._trade_records:
            if r.outcome is not None:
                continue
            m = self._active_markets.get(r.market_condition_id)
            if not m or not m.resolved or not m.resolution:
                continue
            won = r.direction == m.resolution
            r.outcome = "win" if won else "loss"
            r.pnl = (r.size_usd / r.entry_price - r.size_usd) if won else -r.size_usd
            resolved.append(r)
            logger.info(f"{'✅' if won else '❌'} {r.trade_id} | {r.outcome.upper()} | ${r.pnl:+.2f}")
        return resolved

    # ── Stats ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        done = [r for r in self._trade_records if r.outcome]
        if not done:
            return {"total_trades": len(self._trade_records), "completed": 0, "pending": len(self._trade_records), "win_rate": 0.0, "total_pnl": 0.0}
        w = sum(1 for r in done if r.outcome == "win")
        l = len(done) - w
        pnl = sum(r.pnl for r in done)
        return {
            "total_trades": len(self._trade_records), "completed": len(done),
            "pending": len(self._trade_records) - len(done),
            "wins": w, "losses": l, "win_rate": (w / len(done)) * 100,
            "total_pnl": pnl,
        }

    def get_trade_records(self) -> list[TradeRecord]:
        return self._trade_records.copy()
