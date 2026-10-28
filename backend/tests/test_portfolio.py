from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.database.models import Market, PaperOrder, Position, Wallet
from app.paper_trading import portfolio as pf
from app.paper_trading.execution import SCENARIOS, walk

OPT = SCENARIOS["OPTIMISTIC"]


def buy(levels, shares, rate=D(0)):
    return walk(levels, D(shares), None, "BUY", OPT, D("0.01"), rate)


async def _buy(s, w, fill, coid="o1", token="t-yes", market="m1"):
    return await pf.record_buy(s, w, fill, client_order_id=coid, group_id="g1", strategy="test", market_id=market,
                               token_id=token, outcome="Yes", scenario="OPTIMISTIC", latency_ms=0, book={},
                               signal={}, uncertainty=None, exit_rules=None)


async def test_buy_then_settle_win(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", ["test"], D(100))
        f = buy([(D("0.40"), D(100))], 10, D("0.07"))
        o = await _buy(s, w, f)
        assert o.status == "filled"
        assert w.cash == D(100) - D(4) - f.fee
        s.add(Market(id="m1", condition_id="c", question="q", resolved=True, payouts=["1", "0"]))
        await s.flush()
        m = await s.get(Market, "m1")
        assert await pf.settle_market(s, m, {"t-yes": 0, "t-no": 1}) == 1
        assert await pf.settle_market(s, m, {"t-yes": 0, "t-no": 1}) == 0  # idempotent
        pos = (await s.scalars(select(Position))).one()
        assert pos.status == "closed" and pos.realized_pnl == D(10) - D(4) - f.fee
        assert w.cash == D(100) + pos.realized_pnl
        assert await pf.integrity(s, w) == []


async def test_settle_50_50(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", [], D(100))
        await _buy(s, w, buy([(D("0.40"), D(100))], 10))
        s.add(Market(id="m1", condition_id="c", question="q", resolved=True, payouts=["0.5", "0.5"]))
        await s.flush()
        await pf.settle_market(s, await s.get(Market, "m1"), {"t-yes": 0})
        assert w.cash == D(101)


async def test_duplicate_order_rejected(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", [], D(100))
        await _buy(s, w, buy([(D("0.40"), D(100))], 10))
        with pytest.raises(pf.DuplicateOrder):
            await _buy(s, w, buy([(D("0.40"), D(100))], 10))
        assert w.cash == D(96)


async def test_insufficient_cash_rejected_no_negative_balance(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", [], D(1))
        o = await _buy(s, w, buy([(D("0.40"), D(100))], 10))
        assert o.status == "rejected" and w.cash == D(1)
        assert (await s.scalars(select(Position))).all() == []


async def test_partial_sell_splits_position(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", [], D(100))
        await _buy(s, w, buy([(D("0.40"), D(100))], 10))
        pos = (await s.scalars(select(Position))).one()
        sell = walk([(D("0.50"), D(4))], D(10), None, "SELL", OPT, D("0.01"), D(0))
        assert sell.status == "partial"
        await pf.record_sell(s, w, pos, sell, client_order_id="s1", reason="take_profit", scenario="OPTIMISTIC",
                             latency_ms=0, book={})
        rows = (await s.scalars(select(Position).order_by(Position.id))).all()
        assert rows[0].status == "open" and rows[0].shares == 6 and rows[0].cost == D("2.4")
        assert rows[1].status == "closed" and rows[1].realized_pnl == D("0.4")
        assert await pf.integrity(s, w) == []


async def test_state_survives_restart(db, tmp_path):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", [], D(100))
        await _buy(s, w, buy([(D("0.40"), D(100))], 10))
    await db.engine().dispose()
    db.init_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with db.write() as s:
        w = (await s.scalars(select(Wallet))).one()
        assert w.cash == D(96)
        with pytest.raises(pf.DuplicateOrder):  # replaying the same signal after restart is a no-op
            await _buy(s, w, buy([(D("0.40"), D(100))], 10))
    async with db.read() as s:
        assert len((await s.scalars(select(PaperOrder))).all()) == 1
