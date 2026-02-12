"""
╔══════════════════════════════════════════════════════════════════╗
║  EDGE — Arbitrage Scanner + Hedge Engine                         ║
║                                                                    ║
║  Arb:   Buy both sides when UP + DOWN < threshold (free money)   ║
║  Hedge: Buy opposite side when signal flips on open position     ║
║                                                                    ║
║  Both toggled via config: enable_arb / enable_hedge              ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("edge")


@dataclass
class ArbOpportunity:
    """A detected arbitrage: buy both sides for guaranteed profit."""
    market_condition_id: str
    question: str
    price_up: float
    price_down: float
    combined: float          # price_up + price_down (should be < 1.0)
    edge_pct: float          # (1.0 - combined) * 100 — guaranteed profit %
    size_per_side: float     # USD to buy each side
    guaranteed_profit: float # expected profit


@dataclass
class HedgeAction:
    """A hedge: buy the opposite side to lock in a spread."""
    original_trade_id: str
    original_direction: str
    hedge_direction: str
    original_entry: float    # price paid for original side
    hedge_price: float       # current price of opposite side
    locked_profit: float     # guaranteed outcome after hedge
    size_usd: float


class EdgeEngine:
    """
    Arbitrage scanner + hedge engine.

    Arb:
        Every cycle, checks all active markets for mispricing.
        If UP + DOWN < arb_threshold (default 0.98), buys both sides.
        One side resolves to $1.00 → guaranteed profit.

    Hedge:
        Tracks open positions. If the strategy flips direction
        while holding a position, buys the opposite side to
        lock in a spread instead of riding out a potential loss.
    """

    def __init__(self, config):
        self.arb_enabled = config.enable_arb
        self.hedge_enabled = config.enable_hedge
        self.arb_threshold = config.arb_threshold
        self.arb_min_edge_pct = config.arb_min_edge_pct
        self.arb_size_usd = config.arb_size_usd
        self.hedge_min_conf = config.hedge_min_confidence
        self._hedged_trades: set[str] = set()  # trade IDs already hedged

    # ── Arbitrage ───────────────────────────────────────────────

    def scan_arb(self, markets: list) -> list[ArbOpportunity]:
        """
        Scan markets for arbitrage opportunities.

        Returns list of ArbOpportunity when UP + DOWN < threshold.
        """
        if not self.arb_enabled:
            return []

        opportunities = []
        for m in markets:
            if not m.is_tradeable:
                continue

            combined = m.price_up + m.price_down

            if combined < self.arb_threshold:
                edge_pct = (1.0 - combined) * 100
                if edge_pct < self.arb_min_edge_pct:
                    continue

                profit = self.arb_size_usd * (1.0 / combined - 1.0)

                opp = ArbOpportunity(
                    market_condition_id=m.condition_id,
                    question=m.question,
                    price_up=m.price_up,
                    price_down=m.price_down,
                    combined=combined,
                    edge_pct=edge_pct,
                    size_per_side=self.arb_size_usd,
                    guaranteed_profit=round(profit, 2),
                )
                opportunities.append(opp)
                logger.info(
                    f"💰 ARB: {m.question[:50]}... | "
                    f"UP={m.price_up:.3f} + DOWN={m.price_down:.3f} = {combined:.3f} | "
                    f"edge={edge_pct:.1f}% | profit=${profit:.2f}"
                )

        return opportunities

    # ── Hedge ───────────────────────────────────────────────────

    def check_hedge(
        self,
        open_trades: list,
        current_direction: str,
        current_confidence: float,
        markets: dict,
    ) -> list[HedgeAction]:
        """
        Check if any open positions should be hedged.

        Triggers when:
          - Strategy now signals opposite direction from an open trade
          - New signal confidence exceeds hedge_min_confidence
          - The trade hasn't already been hedged
        """
        if not self.hedge_enabled:
            return []

        actions = []
        for trade in open_trades:
            # Skip resolved or already hedged
            if trade.outcome is not None:
                continue
            if trade.trade_id in self._hedged_trades:
                continue

            # Check if signal flipped
            if trade.direction == current_direction:
                continue  # same direction, no hedge needed

            # Confidence check
            if current_confidence < self.hedge_min_conf:
                continue

            # Get current market prices
            market = markets.get(trade.market_condition_id)
            if not market:
                continue

            # Price of the opposite side
            if trade.direction == "up":
                hedge_price = market.price_down
                hedge_dir = "down"
            else:
                hedge_price = market.price_up
                hedge_dir = "up"

            # Calculate locked outcome
            # Original: paid entry_price for original side
            # Hedge: pay hedge_price for opposite side
            # Total cost: entry_price + hedge_price
            # Guaranteed payout: $1.00 (one side wins)
            # Profit: 1.00 - entry_price - hedge_price
            total_cost = trade.entry_price + hedge_price
            locked_profit = (1.0 - total_cost) * trade.size_usd

            action = HedgeAction(
                original_trade_id=trade.trade_id,
                original_direction=trade.direction,
                hedge_direction=hedge_dir,
                original_entry=trade.entry_price,
                hedge_price=hedge_price,
                locked_profit=round(locked_profit, 2),
                size_usd=trade.size_usd,
            )
            actions.append(action)

            if locked_profit > 0:
                logger.info(
                    f"🛡️ HEDGE (lock profit): {trade.trade_id} | "
                    f"original={trade.direction.upper()} @ {trade.entry_price:.3f} | "
                    f"hedge={hedge_dir.upper()} @ {hedge_price:.3f} | "
                    f"locked=${locked_profit:+.2f}"
                )
            else:
                logger.info(
                    f"🛡️ HEDGE (limit loss): {trade.trade_id} | "
                    f"original={trade.direction.upper()} @ {trade.entry_price:.3f} | "
                    f"hedge={hedge_dir.upper()} @ {hedge_price:.3f} | "
                    f"max loss=${locked_profit:.2f} (vs -${trade.size_usd:.2f} unhedged)"
                )

        return actions

    def mark_hedged(self, trade_id: str):
        """Mark a trade as hedged so we don't double-hedge."""
        self._hedged_trades.add(trade_id)
