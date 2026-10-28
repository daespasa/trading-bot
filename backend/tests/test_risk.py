from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS

from app.paper_trading.risk import check

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
W = NS(status="running", max_open_positions=5, max_daily_loss=D(10), cash=D(100))
M = {"m1": NS(closed=False, accepting_orders=True, end_date=NOW + timedelta(days=1))}
SIG = NS(legs=[NS(market_id="m1")])


def test_allows_clean_trade():
    assert check(W, [], D(100), D(100), SIG, True, M, NOW) == []


def test_blocks():
    pos = [NS(group_id=f"g{i}", market_id=f"x{i}") for i in range(5)]
    assert "max open positions reached" in check(W, pos, D(100), D(100), SIG, True, M, NOW)
    assert "daily loss limit reached" in check(W, [], D(90), D(100), SIG, True, M, NOW)
    assert any("feed" in r for r in check(W, [], D(100), D(100), SIG, False, M, NOW))
    assert "already exposed to this market" in check(W, [NS(group_id="g", market_id="m1")], D(100), D(100), SIG, True, M, NOW)
    assert "wallet paused" in check(NS(**{**W.__dict__, "status": "paused"}), [], D(100), D(100), SIG, True, M, NOW)
    assert any("not tradable" in r for r in check(W, [], D(100), D(100), SIG, True, M, NOW + timedelta(days=2)))
