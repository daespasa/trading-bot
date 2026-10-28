# Profitability gates

The code is in `backend/app/analytics/profitability.py` (`GATES`, `gate()`). It is evaluated per (wallet, strategy) every 10 minutes, and every status change is stored as a `gate` system event, which forms the audit trail.

**Unit:** a trade is one opportunity taken by one wallet, with all its legs. Trades sharing an event, or a crypto time window, form one **independent cluster**. Statistics resample clusters, never individual trades.

| Status | Condition |
|---|---|
| RESEARCH | Enabled, but no paper trades and no backtest signals |
| BACKTESTING | Backtest signals exist, no paper trades yet |
| INSUFFICIENT DATA | Fewer than 30 independent clusters, or less than 7 days |
| REJECTED | 95 % CI of the mean cluster PnL entirely below 0, **or** net PnL ≤ 0 with conservative-execution PnL < 0 |
| PAPER TESTING | Enough data, but at least one PROMISING condition fails (inconclusive) |
| PROMISING | All of the following: CI lower bound > 0; CONSERVATIVE PnL > 0; profitable in both halves of the sample; at most 20 % of verifiable fills contradicted by real trades |
| READY FOR HUMAN REVIEW | PROMISING, plus: ≥ 100 clusters; ≥ 28 days; max drawdown ≤ 25 % of capital; positive in ≥ 3 of the last 4 weeks; realised PnL above the market-implied baseline |

## Notes

- The minimum sizes are **necessary, never sufficient**. The decision always rests on the bootstrap CI, on conservative execution and on stability. A strategy can stay in PAPER TESTING forever.
- **The market-implied baseline:** if the mid price at entry were the true probability, a trade's expected PnL is `shares × mid − cost`. A strategy that only matches this baseline has no skill.
- **READY FOR HUMAN REVIEW changes nothing operationally.** Real trading stays disabled (`REAL_TRADING_ENABLED = False`).
- **No annualised return** is shown before 30 days and 30 clusters.
- **Changing parameters** (strategy configs, scenarios) never rewrites recorded orders. A meaningful change should start a new experiment (Paper trading → Restart experiment) so that before and after are not mixed.
