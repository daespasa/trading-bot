# Phase 2 audit: can the simulated profits be trusted?

Date: 2026-10-28. Method: code review, plus queries on a copy of the live database (backup kept at
`data/backups/lab-before-phase2.db`), plus a direct comparison of our order books with the exchange's REST books.

## Questions from the brief

| # | Topic | How it works (file) | Finding |
|---|---|---|---|
| 1 | Polymarket data | Gamma discovery every 60 s, CLOB WebSocket deltas, REST resync every 60 s (`market_data/collector.py`) | 76 of 80 tokens matched the exchange exactly. In the 4 mismatches **our ask was better than the real one**: stale levels left by missed deltas. **Fixed**: fills are now checked against the exchange book (see Execution Validator). |
| 2 | Signals | Pure functions of a market snapshot (`strategies/*`) | No look-ahead found. The book freshness limit was 180 s for every market, too lax for 5–15 min markets. **Fixed**: 20 s for markets ending within 2 h, and crossed books are rejected. |
| 3 | Order simulation | Plan on the book at signal time, wait latency, fill-and-kill against the book (`services/engine.py`, `paper_trading/execution.py`) | Correct, but it trusted our own book. **Fixed**: fills now use the worse of our book and the exchange book. |
| 4 | Prices | Buys walk asks, sells walk bids, never the mid | Correct. |
| 5 | Fees | `C × rate × p × (1−p)` with the documented category rate | Correct (tested against the docs example). Fees are charged in USDC; on the exchange they may be taken in shares (immaterial at this size). |
| 6 | Slippage | Walks real depth; REALISTIC takes 80 % of displayed size, CONSERVATIVE 50 % plus 1 tick | Correct. Historic fills were checked against real trades: see the validator results. |
| 7 | Open positions | `positions` rows; valued at best bid | Correct. Positions in markets no longer monitored would be valued at 0; markets with open positions stay monitored. |
| 8 | Settlement | Official Gamma `umaResolutionStatus=resolved` and `outcomePrices` | Correct after the phase 1 fix (`closed=true` lookup). All 57 closed momentum trades settled by resolution. |
| 9 | PnL | `realized = proceeds − cost` (cost includes entry fees) | Correct. Now reconciled two independent ways (`portfolio.reconcile`). |
| 10 | Drawdown | Peak-to-trough on equity snapshots (every 5 min) | Correct but coarse. The analyzer adds per-strategy drawdown on realised PnL. |
| 11 | Win rate | Per closed trade group | **Misleading in the overview**: it summed wallets, so trades copied by Combined were counted twice ("52 trades"). **Fixed**: the overview labels it, and the Profitability Center evaluates per (wallet, strategy). |
| 12 | Reconnections | Exponential backoff, resubscribe, REST resync; new trades halted while the feed is down | Correct. One "slow consumer" disconnect at startup was observed and recovered. |
| 13 | Persistence | SQLite WAL, Decimal stored as text, Alembic migrations | Correct; a restart test passes. **Fixed**: wallet decisions were memory-only and are now in `decisions`. |
| 14 | Duplicates | Unique `client_order_id = w{wallet}:o{opportunity}:L{leg}`, opportunities reloaded on boot | Correct; tested. |
| 15 | Future information | Features cut at observation time; walk-forward labels resolved before the test window | Correct; tested. |

## Other problems found and fixed

- **The experimental AI bought "lottery tickets"**: 680 shares of "Fed +50 bp" at 0.004, with "edges" of +5,000 %. The model is badly calibrated at extreme prices. Fixed with `max_edge` 0.25 and `min_price` 0.03 on the ML strategies.
- **Statistics treated correlated trades as independent.** BTC and ETH Up/Down for the same window, or several markets of one event, move together. Fixed: confidence intervals now resample clusters (one per event or crypto window).
- **Trade history showed wallet ids** instead of names. Fixed.

## Verdict on the dashboard snapshot (≈+23 USDC, 52 trades, 59.6 % win rate)

- The arithmetic is right: the ledgers reconcile to the cent, with no double settlement and no negative balance.
- **Statistically, it is not evidence.** Rule-Based momentum had 32 closed trades but only **15 independent** ones. The 95 % CI of the mean per independent trade was **[−2.44, +4.79] USDC**, and the second half of the sample lost money.
- Execution assumptions barely matter at this size: CONSERVATIVE still gives +12.6 USDC against +16.8 REALISTIC, because 5 USDC orders are small compared with the displayed depth.
- Real trades corroborated the first 20 historic momentum fills: someone did buy at our price or better within ±30 s. More checks are running.
- At entry prices, the market expected these trades to lose about −7 USDC (the cost of spread and fees). They realised +16.8, so either a real edge or luck: with 15 independent observations we cannot tell which.

**Status: INSUFFICIENT DATA for every strategy.** Nothing has been rejected or promoted yet.
