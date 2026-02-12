<div align="center">

# ₿ BTC-15M-Oracle

**Autonomous BTC prediction bot for Polymarket**

Chainlink-anchored · 5-signal strategy · Live CLOB execution · Arb & hedge engines

```
python bot.py --bankroll 500 --arb --hedge
```

</div>

---

## What This Does

Every 15 minutes, Polymarket runs a binary market: *"Will BTC go up or down?"*

These markets resolve against **Chainlink's BTC/USD data stream** — if the Chainlink price at the end of the window is greater than or equal to the price at the start, UP wins. Otherwise, DOWN wins.

This bot trades those markets autonomously. It wakes up 60 seconds before each window, captures the **Chainlink opening price** (the exact number it needs to beat), analyzes BTC using five technical signals across three price oracles, places a real signed order via Polymarket's CLOB, then goes back to sleep. Rinse and repeat, 96 times a day.

It also has two optional edge engines — an **arbitrage scanner** that catches mispricings between the UP and DOWN sides, and a **hedge module** that protects open positions when the signal flips.

---

## Quickstart

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

**2. Export your Polymarket wallet keys**

Go to [reveal.polymarket.com](https://reveal.polymarket.com) to get your private key, then grab your deposit address from Polymarket's deposit page.

```bash
export POLY_PRIVATE_KEY="your_private_key"
export POLY_FUNDER="0xYourDepositAddress"
export POLY_SIG_TYPE=1
```

> **SIG_TYPE:** `1` = email/Magic login (most common), `2` = browser wallet (MetaMask), `0` = direct EOA

**3. Run**

```bash
python bot.py --bankroll 100
```

The bot is now live, placing real orders with a $100 bankroll. Every 15 minutes it wakes, decides, trades, sleeps.

---

## How It Works

### Timing

The bot is **clock-synced** — it doesn't poll randomly. It calculates the exact second to fire before each 15-minute boundary:

```
11:59:00  →  analyze + trade    (targeting 12:00 boundary)
12:14:00  →  analyze + trade    (targeting 12:15 boundary)
12:29:00  →  analyze + trade    (targeting 12:30 boundary)
12:44:00  →  analyze + trade    (targeting 12:45 boundary)
```

Between windows it polls the clock every 5 seconds. No wasted API calls, no drift.

### Pipeline (each cycle)

```
Anchor ─→ Oracle ─→ Strategy ─→ Risk Check ─→ Market Discovery ─→ [Arb] ─→ [Hedge] ─→ Execute ─→ Resolve
```

**Anchor** — Captures the Chainlink BTC/USD price at the start of the current 15-minute window. This is the **price to beat** — Polymarket resolves UP if the closing Chainlink price >= this number. The bot records it once per window and passes it to the strategy.

**Oracle** — Fetches BTC/USD from three sources: Chainlink (via Polymarket's RTDS websocket at `wss://ws-live-data.polymarket.com`), Binance, and CoinGecko. Chainlink is primary since it's the resolution oracle. Binance and CoinGecko provide redundancy and divergence checks. Rejects stale prices (>30s) and flags divergence >1%.

**Strategy** — Runs five weighted technical signals on 100 recent 15-minute candles:

| Signal | Weight | What it does |
|--------|--------|-------------|
| **Price vs Open** | **35%** | **Where is BTC now relative to the window open? Directly maps to resolution.** |
| Momentum | ~20% | Price delta over N candles — raw directional pressure |
| RSI | ~16% | 14-period Wilder's — detects overbought/oversold extremes |
| MACD | ~16% | 12/26/9 crossover — trend momentum + histogram strength |
| EMA Cross | ~13% | Fast(5) / Slow(15) crossover — short-term trend shift |

When the window opening price is available, Price vs Open takes 35% weight and the other four signals share the remaining 65%. If BTC has already drifted +0.15% above the open, Price vs Open heavily pushes toward UP because that's exactly what Polymarket will check at resolution.

The strategy also applies a **fee-adjusted edge check** — Polymarket charges ~1.5% taker fee at mid-probability. If the expected edge is below the fee threshold, the bot skips the trade even if confidence is above 60%.

Outputs a direction (UP/DOWN/HOLD) and a confidence score. Only trades when confidence ≥ 60% AND edge exceeds estimated fees.

**Risk** — Enforces daily trade cap (20), daily loss limit (15%), consecutive loss cooldown (5 losses → 60min pause), and position sizing via quarter-Kelly criterion.

**Execution** — Uses `py-clob-client` SDK to place an EIP-712 signed market order (fill-or-kill) directly on Polymarket's Central Limit Order Book. Parses the response for actual fill price and transaction hashes.

---

## CLI Reference

```bash
python bot.py --bankroll 500                # Run with $500, 24/7
python bot.py --bankroll 100 --cycles 10    # Run 10 windows then stop
python bot.py --arb-only                    # Arb scanner ONLY — no directional trading
python bot.py --arb-only --dashboard        # Arb scanner + live dashboard
python bot.py --bankroll 200 --arb          # Directional + arb scanner together
python bot.py --bankroll 200 --hedge        # Enable hedge engine
python bot.py --bankroll 500 --arb --hedge  # Everything on
python bot.py --bankroll 500 --dashboard    # Enable live dashboard server
```

| Flag | Default | Description |
|------|---------|-------------|
| `--bankroll` | 500 | Starting capital in USD |
| `--cycles` | 0 | Max entry windows (0 = run forever) |
| `--arb-only` | off | **Run ONLY the arb scanner** — no directional trading, pure gap capture |
| `--arb` | off | Enable arb scanner alongside directional trading |
| `--hedge` | off | Enable hedge engine |
| `--dashboard` | off | Start WebSocket server on :8765 for live dashboard |

**Ctrl+C** → graceful shutdown. Saves performance snapshot, cancels pending orders, closes connections.

---

## Edge Engines

Both off by default. Toggle them with CLI flags.

### Arbitrage (`--arb`)

**Independent fast-polling scanner** — runs its own async loop every ~8 seconds, completely separate from the directional 15-minute trading cycle. Scans BTC markets across **three timeframes**:

- **15-minute** windows
- **30-minute** windows
- **1-hour** windows

On Polymarket, YES + NO should sum to ~$1.00. When they don't, it's free money:

```
YES = $0.45  ·  NO = $0.48  ·  total = $0.93
Buy both sides → one always resolves to $1.00
Profit = $0.07 per share (7.5%) — zero risk, no prediction needed
```

Scanning across multiple timeframes dramatically increases opportunity surface. Gaps on 1-hour markets tend to persist longer than 15-minute ones, giving the scanner more time to capture them.

The scanner has its own daily budget ($200/day default) separate from directional trading capital, its own trade limits, and a per-market cooldown to avoid re-arbing the same market repeatedly.

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `arb_threshold` | 0.98 | Only arb if YES + NO < this |
| `arb_min_edge_pct` | 1.0% | Ignore edges smaller than this |
| `arb_size_usd` | $10 | How much to bet per side |
| `arb_poll_secs` | 8.0 | Scan interval in seconds |
| `arb_max_daily_trades` | 50 | Max arb trade pairs per day |
| `arb_max_daily_budget` | $200 | Max USD committed to arb per day |
| `arb_cooldown_secs` | 120 | Don't re-arb same market within 2 min |
| `arb_timeframes` | 15m, 30m, 1h | Which BTC timeframes to scan |

### Hedge (`--hedge`)

Tracks your open positions. If the strategy flips direction while you're holding, the hedge engine buys the opposite side to lock in a guaranteed outcome:

```
Holding UP @ $0.55 → strategy flips to DOWN (confidence 0.70)
Buy DOWN @ $0.40 → total exposure = $0.95
One side pays $1.00 → locked profit = $0.05 per share
```

If the combined cost exceeds $1.00, the hedge still caps your downside — you lose less than riding out an unhedged position.

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `hedge_min_confidence` | 0.65 | Only hedge if the flip signal is this strong |

---

## Wallet Setup

### Polymarket Email Login *(most users)*

This is the standard Polymarket wallet. No MetaMask needed.

1. Log in at [polymarket.com](https://polymarket.com) with your email
2. Go to **Deposit** → copy your **Deposit Address** → this is `POLY_FUNDER`
3. Go to [reveal.polymarket.com](https://reveal.polymarket.com) → export your key → this is `POLY_PRIVATE_KEY`
4. Set `POLY_SIG_TYPE=1`
5. Make sure your wallet has USDC on Polygon

### Browser Wallet (MetaMask, Coinbase Wallet)

1. Connect your wallet to Polymarket
2. Export your Polygon private key → `POLY_PRIVATE_KEY`
3. Copy your Polymarket proxy address → `POLY_FUNDER`
4. Set `POLY_SIG_TYPE=2`
5. Approve token allowances first (USDC + CTF on Polygon)

### Direct EOA

1. Use any Polygon private key → `POLY_PRIVATE_KEY`
2. No funder needed
3. Set `POLY_SIG_TYPE=0`

---

## Configuration

All tuning parameters live in `config/settings.py`. The defaults are conservative.

### Timing

| Param | Default | What it does |
|-------|---------|-------------|
| `entry_lead_secs` | 60 | How early before the boundary to fire |
| `entry_window_secs` | 30 | How long the entry window stays open |
| `sleep_poll_secs` | 5 | Clock check interval while idle |

### Strategy

| Param | Default | What it does |
|-------|---------|-------------|
| `confidence_threshold` | 0.60 | Minimum score to trade (0–1) |
| `weight_momentum` | 0.30 | Momentum signal weight (scaled to 0.65× when anchor present) |
| `weight_rsi` | 0.25 | RSI signal weight |
| `weight_macd` | 0.25 | MACD signal weight |
| `weight_ema_cross` | 0.20 | EMA crossover weight |
| `price_vs_open_weight` | 0.35 | Weight given to Chainlink open price anchor |
| `fee_threshold_pct` | 1.5 | Skip trades where edge < estimated Polymarket taker fee |

### Risk

| Param | Default | What it does |
|-------|---------|-------------|
| `max_trade_size_usd` | $25 | Hard cap per trade |
| `max_daily_trades` | 20 | Trades per day before stopping |
| `max_daily_loss_pct` | 15% | Daily drawdown circuit breaker |
| `max_consecutive_losses` | 5 | Triggers 60-minute cooldown |
| `kelly_fraction` | 0.25 | Quarter-Kelly position sizing |

### Orders

| Param | Default | What it does |
|-------|---------|-------------|
| `order_type` | "market" | `"market"` (FOK) or `"limit"` (GTC) |
| `max_slippage_pct` | 2.0% | Max slippage before rejecting |
| `min_liquidity_usd` | $50 | Skip markets below this liquidity |

---

## Project Structure

```
btc-15m-oracle/
├── bot.py                        Main orchestrator — clock sync, CLI, trading loop
├── dashboard.jsx                 React dashboard (connects to bot via WebSocket)
│
├── config/
│   └── settings.py               All parameters — strategy, risk, timing, edge
│
├── core/
│   ├── polymarket_client.py      py-clob-client SDK — order signing + execution
│   ├── risk_manager.py           Kelly sizing, daily limits, loss cooldowns
│   ├── arb_scanner.py            Independent fast-polling arb engine (15m/30m/1h)
│   ├── edge.py                   Hedge engine (signal-flip protection)
│   ├── dashboard_server.py       HTTP + WebSocket server for live dashboard
│   └── trade_logger.py           Structured JSONL logging
│
├── oracles/
│   └── price_feed.py             Chainlink-first BTC oracle + window anchor tracking
│
├── strategies/
│   └── signal_engine.py          5-signal strategy with open-price anchor + fee filter
│
├── logs/                         Runtime logs (trades, strategy, oracle, errors)
├── data/                         Performance snapshots
├── .env.example                  Wallet config template
└── requirements.txt              Python dependencies
```

---

## Logs

Every action is logged to structured JSONL for full auditability:

| File | What's in it |
|------|-------------|
| `logs/trades.jsonl` | Every order — direction, size, fill price, order ID, tx hashes |
| `logs/strategy.jsonl` | Every decision — signal values, confidence, drift from open, hold reasons |
| `logs/oracle.jsonl` | Every price fetch — Chainlink price, window open, source prices, spread |
| `logs/errors.log` | Errors with stack traces |
| `data/performance.json` | Latest cumulative stats snapshot |

---

## Safety

The bot has multiple layers of protection to prevent catastrophic losses:

**Chainlink-anchored** — The bot uses the same oracle Polymarket resolves against (Chainlink BTC/USD via RTDS websocket). It knows the exact price it needs to beat, not just a generic direction guess.

**Market restriction** — Hardcoded to BTC 15-minute UP/DOWN binary markets only. It will never trade any other asset or market type.

**Oracle consensus** — Chainlink is primary (resolution oracle), Binance and CoinGecko provide redundancy. Rejects stale data (>30s old). Alerts when source spread exceeds 1%.

**Daily circuit breaker** — If cumulative daily losses exceed 15% of your bankroll, the bot stops trading for the rest of the day.

**Loss streak cooldown** — After 5 consecutive losses, the bot pauses for 60 minutes before resuming.

**Conservative sizing** — Quarter-Kelly criterion with a hard $25 cap per trade. Even at maximum confidence, a single trade won't exceed 5% of capital.

**No keys, no trades** — The CLOB client won't initialize without a valid `POLY_PRIVATE_KEY`. There's no way to accidentally execute orders.

**Full audit trail** — Every oracle query, strategy decision, risk check, and trade execution is logged to JSONL with timestamps. Nothing happens off the record.

---

## Dependencies

```
aiohttp>=3.9.0           Async HTTP + WebSocket for Chainlink RTDS, Binance, Gamma, dashboard server
py-clob-client>=0.34.0   Polymarket CLOB SDK — order signing + execution
web3==6.14.0              Ethereum interaction (pinned to avoid eth-typing conflicts)
python-dotenv>=1.0.0      Environment variable loading
```

---

## Dashboard

The included `dashboard.jsx` is a React component that connects to the bot via WebSocket for live data.

**Start the bot with the dashboard server:**

```bash
python bot.py --bankroll 500 --dashboard
```

This starts a WebSocket server on `ws://localhost:8765`. The dashboard connects automatically and displays live data — BTC price, signals, positions, equity, and strategy decisions — updating after every trading cycle.

**How to use it:**

1. Run the bot with `--dashboard`
2. Open `dashboard.jsx` as a React artifact in Claude, or drop it into any React project
3. It auto-connects to `ws://localhost:8765` and shows live state
4. If no bot is running, it falls back to simulated demo data so you can preview the layout

The dashboard shows: hero profit banner, stat cards (bankroll, P&L, window open price, strategy direction, risk), equity curve, full signal array with Price vs Open, engine status, oracle sources, and the raw decision log from each cycle.

---

## Disclaimer

This bot places real orders with real money on Polymarket prediction markets. Start with a small bankroll you're comfortable losing. Cryptocurrency markets are volatile and prediction markets carry additional risks. Past backtest performance does not guarantee future results. The authors assume no liability for financial losses.
