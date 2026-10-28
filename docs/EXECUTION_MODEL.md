# Execution, fees and accounting model

Everything here is simulation. There is no code path that signs, sends or could send an order.

## Prices

- Buys walk the **ask** side of the live L2 book, sells walk the **bid** side. The mid price is never treated as executable.
- Orders are marketable limit orders with fill-and-kill semantics: fill what exists up to the limit price, cancel the rest.
- Order books come from the public CLOB WebSocket (`book` snapshots + `price_change` deltas), resynced over REST (`POST /books`) every 60 s.
  A book older than `stale_seconds` (180 s) is not tradable. If the WebSocket is silent for more than 30 s, every wallet stops opening positions until it recovers.

## Fees

`fee = shares × feeRate × p × (1 − p)`, rounded to 5 decimals, minimum 0.00001 USDC (docs.polymarket.com/trading/fees, checked 2026‑10‑28).
Rates by category: crypto 0.07, sports/economics/culture/weather/other 0.05, politics/finance/tech/mentions 0.04, geopolitics 0.
Markets with `feesEnabled=false` pay nothing. The category comes from the market's `feeType` (e.g. `crypto_fees_v2`) or its event tags.
Makers pay no fees, but the lab only simulates taker orders, so maker rebates are ignored.
The CONSERVATIVE scenario uses `max(category rate, CLOB base_fee/10000)` to cover the case where the token's `base_fee` is the binding rate.

## Scenarios

| Scenario | Latency | Depth we assume we get | Adverse ticks | Fee rate | Extra required edge |
|---|---|---|---|---|---|
| OPTIMISTIC | 0 ms | 100 % of displayed size | 0 | documented | 0 |
| REALISTIC (formerly BASE) | 300 ms | 80 % | 0 | documented | 0 |
| CONSERVATIVE | 1000 ms | 50 % | 1 tick per fill | max(documented, base_fee) | 0.01 |

All parameters are editable in Settings. Changes apply to new orders only; recorded orders keep the scenario they were filled with, and the historical label "BASE" means REALISTIC.

- **Latency.** The order is planned on the book at signal time. The engine then waits the latency and fills against the *live book at that moment*, so moves during the latency hit the result.
- **Depth fraction.** Other takers compete for the same displayed liquidity, and our queue position is unknowable. Each order records that assumption in its `uncertainty` field.
- **Sizing.** Size is capped so the worst case at the limit prices, fees included, never exceeds the per-trade budget (5 USDC by default). Orders below the market's `orderMinSize` (usually 5 shares) are rejected, as on the real exchange.
- **Arbitrage legs** use equal share counts. After the latency each leg fills on its own, so one leg can fill and another not. The order is then flagged "legs filled unequally: residual exposure is unhedged" and the unhedged position is held, as in reality.

## Execution Validator (phase 2)

- **Exchange book at fill time.** After the latency the engine fetches the token's book from the CLOB REST API and walks both books: ours (WebSocket) and the exchange's. The wallet gets the **worse** fill. Both are stored in `execution_checks` (`rest_book`: match / ws_better / ws_worse). If the REST call fails, the order says "exchange book not verified".
- **Real trades afterwards.** Every 10 minutes, filled buys older than 2 minutes are compared with the Data API trades of the same token within ±30 s. A real taker buy at our price or better means *corroborated*; only worse prices means *contradicted*; no trades means *unverifiable*. Throttled, and stops on HTTP 429.
- **Freshness.** Markets ending within 2 hours need books younger than 20 s. Crossed books (bid ≥ ask) are rejected, since they mean missed updates.
- **Lifecycle.** detected → executable → submitted → filled → position opened → closed → settled. Each stage can be reconstructed per order (`GET /api/orders/{id}/trace`, and the magnifier icon in the Profitability Center).

## Settlement

Positions are held until official resolution (Gamma `umaResolutionStatus == "resolved"`) and then pay `outcomePrices[i]` per share (1/0, or 0.5/0.5 for a 50‑50 resolution).
Mean-reversion positions may also exit early at the bid (take-profit, stop-loss or time stop).
Two shortcuts are not modelled: early merging of YES+NO pairs back into USDC, and redemption gas.

## Accounting invariants

- Money uses `Decimal` and is stored as exact decimal text in SQLite (swap the column type to `Numeric` for PostgreSQL).
- `cash == initial − Σ position cost + Σ closed proceeds` and `cash ≥ 0` are checked every 5 minutes; violations show up under `loop_errors` in `/api/health`.
- Idempotency: `client_order_id = w{wallet}:o{opportunity}:L{leg}` is unique in the database. The opportunity id survives restarts, because opportunities seen in the last 10 minutes are reloaded at boot, so replaying a signal after a restart cannot duplicate an order.
- Daily loss limit: no *new* positions once `equity at UTC day start − current equity ≥ max_daily_loss`. Positions already open can still lose more than the limit (up to 5 × 5 USDC).
