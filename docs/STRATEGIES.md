# Strategies

All strategies implement `Strategy.evaluate(ctx, cfg) -> list[Signal]` (`backend/app/strategies/base.py`). They read an immutable view of the market state and never touch wallets.
A `Signal` carries legs with limit prices, gross edge, estimated costs, net edge, liquidity, risk, whether the payoff is **guaranteed**, and an `actionable` flag. Non-actionable signals are stored as discarded opportunities with the reason.

| Name | Family | Rule | Guaranteed? |
|---|---|---|---|
| `arb_yes_no` | arbitrage | ask(YES)+ask(NO)+fees < 1, sized by walking both books | Yes: one pair pays exactly 1 in every resolution, including 50‑50 |
| `arb_multi` | arbitrage | negRisk events. All YES: Σ asks < 1. All NO: Σ asks < n−1 | All NO: yes, by mutual exclusivity alone. All YES: only if the open set is **provably exhaustive** (not augmented, all open markets monitored, every closed sibling resolved NO); otherwise logged as speculative |
| `related_markets` | arbitrage | threshold ladders in one event: if A ⇒ B, buy YES(B)+NO(A) when it costs < 1 | Only for the same event, the same end date and the same resolution text (numbers removed). Anything else is "correlation", never traded |
| `crypto_momentum` | rule | BTC/ETH markets. Fair P from a log-normal model on Binance 1m data with realised vol and a momentum drift. TWAP-resolved 5m/15m markets use the elapsed window average plus a Binance↔Chainlink basis uncertainty (3 bps). Edges above `max_edge` (0.25 per share) are treated as model error | No |
| `mean_reversion` | rule | outcome mid fell ≥ 0.10 within 30 min and > 24 h remain: buy, fair = mid + 0.4·move. TP +0.04, SL −0.06, 6 h time stop | No |
| `ai_lightgbm`, `ai_logistic` | ml | buy when calibrated P(model) − ask − fee ≥ 0.05; only while the model kind is ACTIVE | No |

## What we expect, and what was observed

- YES/NO and multi-outcome arbitrage should be extremely rare. Polymarket's CLOB matches complementary orders (mint/merge), so ask(YES)+ask(NO) < 1 is normally arbitraged away by the exchange itself. Seeing none is the expected, correct result, not a bug.
- On the first live run, the momentum model disagreed strongly with prices on 5‑minute Up/Down markets. Those markets resolve on a Chainlink **TWAP**, and the first version modelled a point price. Fixing that (elapsed-window average, basis uncertainty, `max_edge` guard) reduced but did not remove the disagreement. The paper results will show whether the remaining "edge" is real or model error; early trades lost money.
