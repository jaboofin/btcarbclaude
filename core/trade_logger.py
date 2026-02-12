"""
╔══════════════════════════════════════════════════════════════════╗
║  TRADE LOGGER — Structured Logging for All Bot Actions           ║
║  JSONL format for every trade, signal, oracle read, and error    ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json
import time
import os
import logging
from pathlib import Path
from typing import Any

from config.settings import LoggingConfig


class TradeLogger:
    """
    Structured logger writing JSONL files for every bot action.
    
    Separate log streams:
      - trades.jsonl: All trade entries, exits, and resolutions
      - strategy.jsonl: Every strategy decision (even HOLDs)
      - oracle.jsonl: Price feeds and consensus records
      - errors.log: Standard error log
    """

    def __init__(self, config: LoggingConfig):
        self.config = config
        
        # Ensure log directories exist
        for path in [config.trade_log_file, config.strategy_log_file,
                     config.oracle_log_file, config.error_log_file,
                     config.performance_file]:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Configure Python logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(name)-12s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(config.error_log_file),
            ],
        )

    def _write_jsonl(self, filepath: str, data: dict):
        """Append a JSON line to the specified file."""
        data["_ts"] = time.time()
        data["_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(filepath, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")

    def log_trade(self, trade_data: dict):
        """Log a trade event."""
        trade_data["_event"] = "trade"
        self._write_jsonl(self.config.trade_log_file, trade_data)

    def log_strategy(self, strategy_data: dict):
        """Log a strategy decision."""
        strategy_data["_event"] = "strategy"
        self._write_jsonl(self.config.strategy_log_file, strategy_data)

    def log_oracle(self, oracle_data: dict):
        """Log an oracle price read."""
        oracle_data["_event"] = "oracle"
        self._write_jsonl(self.config.oracle_log_file, oracle_data)

    def log_resolution(self, resolution_data: dict):
        """Log a market resolution."""
        resolution_data["_event"] = "resolution"
        self._write_jsonl(self.config.trade_log_file, resolution_data)

    def log_risk_event(self, risk_data: dict):
        """Log a risk management event (cooldown, limit hit, etc)."""
        risk_data["_event"] = "risk"
        self._write_jsonl(self.config.trade_log_file, risk_data)

    def save_performance(self, perf_data: dict):
        """Save current performance snapshot."""
        perf_data["_saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self.config.performance_file, "w") as f:
            json.dump(perf_data, f, indent=2, default=str)

    def get_trade_history(self) -> list[dict]:
        """Read all trade records from JSONL."""
        records = []
        path = self.config.trade_log_file
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        return records
