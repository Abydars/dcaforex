# XAUUSD SMC Scalping Bot

A disciplined SMC/ICT-based scalping bot for gold (XAUUSD) on MT5/Exness.

> **Branch:** `smc-xauusd` — complete rewrite of the original DCA bot with SMC methodology, no martingale, single-position risk management.

---

## Strategy Overview

**Multi-timeframe SMC entry:**

| Timeframe | Role |
|---|---|
| **H1** | Directional bias (BOS / CHoCH based) |
| **M15** | Structural alignment + Premium/Discount zone validation |
| **M5** | Entry trigger: liquidity sweep + FVG retest |

**Entry conditions (ALL must be true):**

1. H1 structure shows clear BOS in one direction (BULLISH or BEARISH)
2. M15 structure aligned with H1 (continuation or fresh CHoCH in H1 direction)
3. Current price is in the discount zone (for longs) or premium zone (for shorts) of the last M15 impulsive leg
4. Recent M5 liquidity sweep in the bias direction (wick beyond prior swing + close back inside)
5. Unmitigated M5 FVG formed after the sweep, still reachable
6. Projected RR ≥ 2.0

**Exit:** Hard SL beyond sweep point. TP at next opposing liquidity (swing high/low). No trailing, no DCA, no averaging.

---

## Risk Rules (non-negotiable)

- Fixed % risk per trade (default 0.5%)
- Max 3 trades per day
- Max 2 consecutive losses → daily stop
- Max 2% daily drawdown → daily stop
- Single open position at a time
- Every order has SL/TP attached at entry

---

## File Structure

```
├── config.py            # All tunable parameters
├── mt5_connector.py     # MT5 init, symbol resolution
├── structure.py         # Swing detection, BOS/CHoCH, P/D zones
├── liquidity.py         # FVG + sweep detection
├── bias.py              # H1 + M15 bias aggregator
├── filters.py           # Session, news, ATR, spread filters
├── risk.py              # Position sizing + daily limits
├── signal_engine.py     # Orchestrates everything → TradeSignal
├── execution.py         # Market orders with SL/TP
├── trade_log.py         # SQLite journaling
├── main.py              # Main bot loop
├── inspect_market.py    # Standalone diagnostic tool
└── tests_structure.py   # Unit tests (no MT5 required)
```

---

## Setup

1. **Install Python 3.10+** (MetaTrader5 package requires Windows)

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create `.env`:**
   ```bash
   cp .env.example .env
   # Edit .env with your MT5 credentials
   ```

4. **Run unit tests (no MT5 needed):**
   ```bash
   python tests_structure.py
   ```

5. **Inspect market state (no trades):**
   ```bash
   python inspect_market.py
   ```
   Shows current H1 trend, M15 zone, M5 sweeps/FVGs, filter states, and whether a signal would fire.

6. **Run the bot:**
   ```bash
   python main.py
   ```

---

## Pre-Live Checklist

Before moving from demo to live, you should have:

- [ ] 100+ trades logged on demo
- [ ] Min 2 months forward-test duration
- [ ] Positive expectancy confirmed on the trade log
- [ ] Max drawdown within acceptable range (< 15%)
- [ ] Winrate ≥ 40% (strategy is low-winrate, high-RR by design)
- [ ] Tested across different market conditions (trend, range, news weeks)

**Do not skip this step.** The previous DCA version looked profitable on demo and lost on live — the only way to avoid that is statistical confidence from a large sample.

---

## Tuning Knobs

Defaults are conservative. Once you've validated the base strategy, you can experiment with:

- `FRACTAL_LEFT` / `FRACTAL_RIGHT` — larger = fewer but higher-quality swings
- `FVG_MIN_SIZE_USD` — larger = filter out noise FVGs
- `SWEEP_MIN_WICK_USD` — larger = only count decisive sweeps
- `MIN_RR` — 2.0 is the floor; can raise to 2.5-3.0 for higher selectivity
- `SESSIONS_UTC` — restrict to just London or just NY if data shows one is better

**Do NOT** change these without running 50+ trades on the new config to validate.

---

## What Was Removed From the Original Repo

| Component | Reason |
|---|---|
| DCA / martingale logic | Mathematically guaranteed to produce eventual ruin |
| Multi-symbol correlation groups | XAUUSD-only now |
| Dashboard UI / Flask server | Not needed for headless trading |
| `signal_state.py` | Dashboard-only state management |
| `auto_params.py` | DCA-specific sizing |
| Pyramid / basket logic | Single position only |

---

## Philosophy

This bot is built on the belief that **genuine edge comes from the entry, not from position management tricks**. Every feature here exists to either:

1. Filter for high-probability setups (sessions, news, structure, zones)
2. Protect capital from catastrophic loss (daily limits, fixed SL)
3. Enable honest evaluation (trade log, inspector tool)

There is no feature designed to "rescue" a bad entry. If a trade is wrong, it loses a small, fixed amount. That's the deal.
