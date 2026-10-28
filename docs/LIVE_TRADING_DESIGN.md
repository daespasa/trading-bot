# Future real-money execution: design only

**Status: forbidden in this phase.** No wallet, key, signature or order-sending code exists. `REAL_TRADING_ENABLED = False` is asserted on every risk check.

## Already in place, and reusable

- **Kill switch** (Settings): stops every wallet from opening positions immediately. Detection and exits keep running.
- **Pre-trade risk checks** (`paper_trading/risk.py`): per-trade size, maximum open positions, daily loss, feed health and market tradability.
- **Idempotent client order ids**, so a restart cannot double-send.
- **Audit trail**: opportunities, per-wallet decisions, orders with the books seen at decision and fill time, exchange-book checks, real-trade checks, gate status changes and system events.
- **The venue interface** (`app/execution/venue.py`): `submit`, `cancel_all`, `balances`. A live adapter would replace only the fill source; strategies, risk and accounting stay the same.

## Preconditions before writing any live adapter

1. At least one strategy is in READY FOR HUMAN REVIEW, and a human has reviewed it.
2. Its out-of-sample paper results, under CONSERVATIVE execution, are positive.
3. Its legal status is confirmed for a resident of Spain. For example, Polymarket has no known DGOJ licence, and crypto exchanges need a MiCA CASP authorisation.
4. Tax treatment is understood (IRPF savings base; Modelo 720/721 when it applies).
5. Hard limits are configured: maximum capital, per trade, per day and total drawdown, all enforced by the venue adapter itself and not only by the strategy.
6. Every cycle, the local ledger is reconciled against venue balances, and trading halts on any mismatch.
7. Real fills are compared with the paper fills of the same signals. If real results are materially worse, trading stops.
8. The owner authorises it explicitly, in writing, for a defined capital amount.

Automation is **not** a guarantee of income. The most likely result of any strategy is "no demonstrable edge".
