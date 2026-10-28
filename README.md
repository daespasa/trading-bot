# Polymarket AI Trading Lab (paper trading only)

A lab that answers one question with real data: **would any automated strategy have beaten Polymarket prices after costs?**
It collects live Polymarket data, runs arbitrage, rule-based and machine-learning strategies against it, and simulates every trade with fictitious USDC under explicit execution assumptions. Results are reported together with their statistical uncertainty.

> No wallet, private key, signature, deposit or real order exists anywhere in this code. `REAL_TRADING_ENABLED = False` is a constant asserted by the risk engine.
> Only public, unauthenticated, read-only endpoints are used (Gamma, CLOB REST/WebSocket, Binance public klines). Spain is not on Polymarket's geoblock list for order placement (docs checked 2026‑10‑28), and this lab places no orders anyway.

## Run

```bash
cp .env.example .env        # optional
docker compose up -d --build
```

Open http://localhost:3000 (Spanish by default; switch ES/EN in the top bar, remembered per browser). The API is at http://localhost:8000/api, and both ports are bound to 127.0.0.1 only.
If port 3000 is taken, use `FRONTEND_PORT=3001 docker compose up -d --build`.
Data lives in `./data` (SQLite in WAL mode, CSV datasets, Markdown reports) and models in `./models`, so restarts and rebuilds keep everything.

### Development without Docker

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATA_DIR=../data MODELS_DIR=../models .venv/bin/alembic upgrade head
DATA_DIR=../data MODELS_DIR=../models .venv/bin/uvicorn app.main:app --port 8000
.venv/bin/pytest            # tests, no network needed
cd ../frontend && npm install && npm run dev
```

## Architecture

One backend process (FastAPI + asyncio) runs all modules as cooperative tasks. ML training runs in a separate niced subprocess.

```
Gamma API ─ discovery (60 s) ─┐
CLOB WebSocket ─ live L2 books ┤→ MarketState (memory) ─→ snapshots (SQLite, retention)
CLOB REST ─ resync (60 s) ─────┘            │
Binance klines (20 s) ──────────────────────┤
                                            ↓
                 Strategy engine (every 5 s, same state for all strategies)
     arb_yes_no · arb_multi · related_markets · crypto_momentum · mean_reversion · ai_lightgbm · ai_logistic
                                            ↓ signals → opportunities table
                          Risk engine (per wallet) → paper execution (latency, depth, fees)
                                            ↓
          Virtual wallets → positions → settlement on official resolution → equity, analytics, reports
                                            ↓
                         FastAPI REST + WebSocket (batched every 2 s) → Next.js dashboard
```

| Path | Contents |
|---|---|
| `backend/app/market_data` | Polymarket client, WebSocket collector, L2 book, Binance feed |
| `backend/app/strategies` | common `Strategy` interface and the 7 strategies |
| `backend/app/paper_trading` | execution maths, accounting, risk checks |
| `backend/app/machine_learning` | features, datasets, walk-forward training, live predictor |
| `backend/app/analytics` | metrics, statistical evidence, reports, backtest replay |
| `backend/app/services/engine.py` | orchestrator, scheduling, recovery, resource monitor |
| `frontend/app/*` | dashboard pages (Next.js App Router, shadcn/ui, TanStack Query/Table/Virtual, Recharts) |

Further reading:
- [docs/EXECUTION_MODEL.md](docs/EXECUTION_MODEL.md): fees, scenarios, sizing, settlement and accounting invariants.
- [docs/ML_METHODOLOGY.md](docs/ML_METHODOLOGY.md): data, leakage prevention, walk-forward validation and the activation gate.
- [docs/STRATEGIES.md](docs/STRATEGIES.md): what each strategy does, and why some "opportunities" are logged but never traded.
- [docs/AUDIT_PHASE2.md](docs/AUDIT_PHASE2.md): can the simulated profits be trusted? Audit findings and fixes.
- [docs/PROFITABILITY_GATES.md](docs/PROFITABILITY_GATES.md): how a strategy moves from research to "ready for human review".
- [docs/OPPORTUNITY_RESEARCH.md](docs/OPPORTUNITY_RESEARCH.md): ranking of other markets worth researching.
- [docs/LIVE_TRADING_DESIGN.md](docs/LIVE_TRADING_DESIGN.md): what must exist before any real money (design only).

## Experimental design

- Five wallets start with 100 USDC each: Arbitrage, Rule-Based, LightGBM, Logistic Regression and Combined. They see identical market state at identical times. Opportunity counts per strategy are reported, because strategies do not get the same number of chances.
- Default limits: 5 USDC per trade, 5 open positions, no new trades after a 10 USDC daily loss. Wallets can be created, paused, resumed and reset from the UI. A reset archives the old wallets rather than deleting them.
- A result counts as evidence only with **≥ 30 closed trades and a bootstrap 95 % CI of mean PnL per trade above zero**. Otherwise the UI and reports say "insufficient evidence" or "not distinguishable from zero".

## Resource profiles

| | `RESOURCE_PROFILE=low` (default) | `normal` |
|---|---|---|
| Markets monitored | 20 (max 50) | 50 (max 200) |
| Snapshot interval | 60 s | 20 s |
| Snapshot retention | 7 days | 30 days |
| ML threads | 2 | 4 |

Measured on the target laptop (i5‑8350U, low profile, 20 markets): backend ~130–180 MB RSS and ~10–15 % of one core at ~1,000 WebSocket messages/s; frontend ~30 MB.
Container limits are 2 GB / 3 CPUs for the backend (including training) and 512 MB for the frontend. Change them in `.env`.

## Security

Ports listen on localhost only. Before exposing the lab to a network, set `API_TOKEN`: the API and WebSocket then require `Authorization: Bearer <token>` (or `?token=`), and the token is entered once in Settings.
