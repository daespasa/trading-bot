"""Pre-trade risk checks. Returns the list of reasons a trade must not happen (empty = allowed)."""
from datetime import datetime
from decimal import Decimal

from app.core.config import REAL_TRADING_ENABLED


def check(wallet, open_positions, equity_now: Decimal, day_start_equity: Decimal, signal, feed_healthy: bool,
          markets: dict, now: datetime) -> list[str]:
    assert REAL_TRADING_ENABLED is False
    reasons = []
    if wallet.status != "running":
        reasons.append(f"wallet {wallet.status}")
    if not feed_healthy:
        reasons.append("market data feed unhealthy: trading halted")
    if len({p.group_id for p in open_positions}) >= wallet.max_open_positions:
        reasons.append("max open positions reached")
    leg_markets = {leg.market_id for leg in signal.legs}
    if leg_markets & {p.market_id for p in open_positions}:
        reasons.append("already exposed to this market")
    if day_start_equity - equity_now >= wallet.max_daily_loss:
        reasons.append("daily loss limit reached")
    if wallet.cash < Decimal(1):
        reasons.append("insufficient cash")
    for mid in leg_markets:
        m = markets.get(mid)
        if m is None or m.closed or not m.accepting_orders or (m.end_date and m.end_date <= now):
            reasons.append(f"market {mid} not tradable")
    return reasons
