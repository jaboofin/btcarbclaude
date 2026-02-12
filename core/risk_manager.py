"""
╔══════════════════════════════════════════════════════════════════╗
║  RISK MANAGER — Capital, Position Sizing, and Loss Controls      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import logging
from dataclasses import dataclass

from config.settings import RiskConfig

logger = logging.getLogger("risk")


@dataclass
class DailyStats:
    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    consecutive_losses: int = 0
    last_trade_time: float = 0.0
    cooldown_until: float = 0.0


class RiskManager:
    def __init__(self, config: RiskConfig, capital: float):
        self.config = config
        self.capital = capital
        self._daily = DailyStats(date=self._today())
        self._total_pnl = 0.0

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _reset_daily_if_needed(self):
        today = self._today()
        if self._daily.date != today:
            logger.info(f"New day — resetting. Previous: {self._daily}")
            self._daily = DailyStats(date=today)

    def can_trade(self) -> tuple[bool, str]:
        self._reset_daily_if_needed()

        if time.time() < self._daily.cooldown_until:
            remaining = int(self._daily.cooldown_until - time.time())
            return False, f"Cooldown ({remaining}s remaining)"

        if self._daily.trades >= self.config.max_daily_trades:
            return False, f"Daily limit ({self.config.max_daily_trades})"

        if self.capital > 0:
            daily_loss_pct = abs(min(0, self._daily.total_pnl)) / self.capital * 100
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                return False, f"Daily loss limit ({daily_loss_pct:.1f}%)"

        if self._daily.consecutive_losses >= self.config.max_consecutive_losses:
            self._daily.cooldown_until = time.time() + (self.config.loss_streak_cooldown_mins * 60)
            return False, f"Loss streak ({self.config.max_consecutive_losses}) — cooldown"

        if self.capital <= 0:
            return False, "No capital"

        return True, "OK"

    def calculate_position_size(self, confidence: float) -> float:
        if self.capital <= 0:
            return 0.0
        kelly = max(0, 2 * confidence - 1)
        fractional_kelly = kelly * self.config.kelly_fraction
        size = self.capital * fractional_kelly
        size = min(size, self.capital * (self.config.max_trade_pct / 100))
        size = min(size, self.config.max_trade_size_usd)
        size = max(size, self.config.min_trade_size_usd)
        size = min(size, self.capital)
        return round(size, 2)

    def record_trade(self, pnl: float):
        self._reset_daily_if_needed()
        self._daily.trades += 1
        self._daily.total_pnl += pnl
        self._daily.last_trade_time = time.time()
        self._total_pnl += pnl

        if pnl >= 0:
            self._daily.wins += 1
            self._daily.consecutive_losses = 0
        else:
            self._daily.losses += 1
            self._daily.consecutive_losses += 1
            self.capital += pnl
            if self._daily.consecutive_losses >= self.config.max_consecutive_losses:
                logger.warning(f"⚠️ {self._daily.consecutive_losses} consecutive losses — cooldown")

        logger.info(
            f"Risk: trades={self._daily.trades} W/L={self._daily.wins}/{self._daily.losses} "
            f"pnl=${self._daily.total_pnl:+.2f} capital=${self.capital:.2f}"
        )

    def get_status(self) -> dict:
        self._reset_daily_if_needed()
        can, reason = self.can_trade()
        return {
            "can_trade": can, "reason": reason, "capital": self.capital,
            "daily_trades": self._daily.trades, "daily_pnl": self._daily.total_pnl,
            "consecutive_losses": self._daily.consecutive_losses,
            "in_cooldown": time.time() < self._daily.cooldown_until,
            "total_pnl": self._total_pnl,
        }
