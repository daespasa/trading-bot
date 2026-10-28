"""Phase 2: execution validator, verifiable accounting, profitability analyzer and gates.
All market data here is SYNTHETIC and exists only to test the mechanics."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.analytics import profitability as prof
from app.analytics.validation import classify_prints, compare_fills, worse_fill
from app.database import session
from app.database.models import Decision, ExecutionCheck, Market, PaperOrder, Position, Wallet
from app.market_data.book import Book
from app.market_data.collector import MarketView
from app.paper_trading import portfolio as pf
from app.paper_trading.execution import SCENARIOS, configure_scenarios, walk
from app.services.engine import Engine
from app.strategies import REGISTRY
from app.strategies.base import Context

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
OPT, REAL = SCENARIOS["OPTIMISTIC"], SCENARIOS["REALISTIC"]
T = D("0.01")


# ---------- execution validator ----------
def test_compare_and_worse_fill():
    ws = walk([(D("0.40"), D(100))], D(10), None, "BUY", OPT, T, D(0))
    rest = walk([(D("0.42"), D(100))], D(10), None, "BUY", OPT, T, D(0))
    assert compare_fills(ws, rest) == "ws_better"
    assert worse_fill(ws, rest) is rest
    thin = walk([(D("0.40"), D(4))], D(10), None, "BUY", OPT, T, D(0))
    assert worse_fill(ws, thin) is thin  # fewer shares available on the exchange wins
    assert compare_fills(ws, ws) == "match"


def test_print_classification():
    t0 = 1_000_000.0
    trades = [{"asset": "a", "side": "BUY", "price": 0.41, "timestamp": t0 + 5}]
    assert classify_prints(trades, "a", t0, D("0.41"), T)[0] == "corroborated"
    assert classify_prints(trades, "a", t0, D("0.39"), T)[0] == "contradicted"
    assert classify_prints(trades, "a", t0 + 500, D("0.41"), T)[0] == "unverifiable"
    assert classify_prints(trades, "b", t0, D("0.41"), T)[0] == "unverifiable"


# ---------- dependence-aware statistics ----------
def test_btc_and_eth_same_window_are_one_cluster():
    end = NOW
    btc = NS(category="crypto", slug="btc-updown-5m-1", end_date=end, event_id="e1")
    eth = NS(category="crypto", slug="eth-updown-5m-1", end_date=end, event_id="e2")
    other = NS(category="politics", slug="x", end_date=end, event_id="e9")
    assert prof.cluster_key(btc, "1") == prof.cluster_key(eth, "2")
    assert prof.cluster_key(other, "3") == "event:e9"


def test_cluster_bootstrap_ci():
    r = prof.cluster_bootstrap([1.0] * 40)
    assert r["ci95"] == [1.0, 1.0] and r["clusters"] == 40
    noisy = prof.cluster_bootstrap([5.0, -5.0] * 20)
    assert noisy["ci95"][0] < 0 < noisy["ci95"][1]


def _trade(i, realized, fees=0.1, cluster=None, closed=True, cost=5.0):
    t = NOW - timedelta(days=10) + timedelta(hours=i)
    scen = {name: {"pnl": realized - (0.5 if name == "CONSERVATIVE" else 0), "complete": True} for name in SCENARIOS}
    return {"wallet_id": 1, "group_id": f"g{i}", "strategy": "s", "closed": closed, "opened_at": t,
            "closed_at": t + timedelta(minutes=5) if closed else None, "cluster": cluster or f"c{i}", "market_id": "m",
            "question": "q", "cost": cost, "fees": fees, "proceeds": cost + realized, "realized": realized,
            "open_cost": 0.0 if closed else cost, "unrealized_bid": 0.0, "market_ev": -fees, "scenarios": scen,
            "checks": {}, "duration_min": 5.0}


def test_metrics_profit_factor_expectancy_win_rate():
    ts = [_trade(0, 2.0), _trade(1, -1.0), _trade(2, 1.0), _trade(3, -1.0)]
    m = prof.metrics(ts, 100.0, NOW)
    assert m["win_rate"] == 0.5 and m["profit_factor"] == 1.5 and m["expectancy"] == 0.25
    assert m["net_pnl"] == 1.0 and abs(m["gross_pnl"] - 1.4) < 1e-9 and abs(m["fees"] - 0.4) < 1e-9
    assert m["annualized"] is None  # never annualise 10 days of data


def test_profitable_before_costs_loses_after():
    # each trade earns 0.05 before fees but pays 0.10 of fees: gross > 0, net < 0
    ts = [_trade(i, -0.05, fees=0.10) for i in range(40)]
    m = prof.metrics(ts, 100.0, NOW)
    assert m["gross_pnl"] > 0 > m["net_pnl"]
    assert m["cost_sensitivity"]["fees_x0"] > 0 > m["cost_sensitivity"]["fees_x1"]
    assert prof.gate(m, False)[0] == "REJECTED"


def test_gates():
    few = prof.metrics([_trade(i, 1.0) for i in range(5)], 100.0, NOW)
    assert prof.gate(few, False)[0] == "INSUFFICIENT DATA"
    # 40 trades but all in 3 clusters: still not enough independent evidence
    clustered = prof.metrics([_trade(i, 1.0, cluster=f"w{i % 3}") for i in range(40)], 100.0, NOW)
    assert clustered["clusters"] == 3 and prof.gate(clustered, False)[0] == "INSUFFICIENT DATA"
    good = prof.metrics([_trade(i, 1.0 + (i % 3) * 0.1) for i in range(40)], 100.0, NOW)
    status, why = prof.gate(good, False)
    assert status == "PROMISING", why  # positive everywhere but sample too short for human review
    assert prof.gate(prof.metrics([], 100.0, NOW), True)[0] == "BACKTESTING"
    assert prof.gate(prof.metrics([], 100.0, NOW), False)[0] == "RESEARCH"


def test_scenarios_configurable_and_validated():
    configure_scenarios({"REALISTIC": {"latency_ms": 500, "depth_fraction": 0.6}})
    assert SCENARIOS["REALISTIC"].latency_ms == 500 and SCENARIOS["REALISTIC"].depth_fraction == D("0.6")
    with pytest.raises(ValueError):
        configure_scenarios({"CONSERVATIVE": {"depth_fraction": 3}})
    configure_scenarios({})
    assert SCENARIOS["REALISTIC"].latency_ms == 300


# ---------- data quality guards ----------
def _mv(end):
    return MarketView("m", "Q?", None, None, None, "other", False, False, end, None, False, D(0), 0, D("0.01"), D(5),
                      True, False, "m", "", ["y", "n"], ["Yes", "No"])


def test_short_horizon_books_must_be_fresh_and_uncrossed():
    b = Book("y")
    b.replace([{"price": "0.40", "size": "100"}], [{"price": "0.42", "size": "100"}], None, "ws")
    b.ts_received = NOW.timestamp() - 30
    ctx = Context(NOW, {}, {"y": b}, {}, {})
    assert ctx.fresh_book("y", _mv(NOW + timedelta(days=3))) is b  # 30 s is fine for a market days away
    assert ctx.fresh_book("y", _mv(NOW + timedelta(minutes=30))) is None  # too old for a market ending soon
    b.ts_received = NOW.timestamp()
    b.replace([{"price": "0.45", "size": "100"}], [{"price": "0.42", "size": "100"}], None, "ws")
    b.ts_received = NOW.timestamp()
    assert ctx.fresh_book("y", _mv(NOW + timedelta(days=3))) is None  # crossed book = missed updates


def test_ai_rejects_longshots_and_absurd_edges():
    m = _mv(NOW + timedelta(days=3))
    yes, no = Book("y"), Book("n")
    yes.replace([{"price": "0.01", "size": "1000"}], [{"price": "0.02", "size": "1000"}], None, "ws")
    no.replace([{"price": "0.97", "size": "1000"}], [{"price": "0.98", "size": "1000"}], None, "ws")
    yes.ts_received = no.ts_received = NOW.timestamp()
    pred = {("lightgbm", "m"): {"p": 0.6, "p_market": 0.015, "model_id": 1, "status": "ACTIVE", "ts": NOW}}
    ctx = Context(NOW, {"m": m}, {"y": yes, "n": no}, {}, {}, predictions=pred)
    sigs = REGISTRY["ai_lightgbm"].evaluate(ctx, dict(REGISTRY["ai_lightgbm"].defaults))
    assert sigs and not any(s.actionable for s in sigs)
    assert any("longshot" in (s.reason or "") or "max_edge" in (s.reason or "") for s in sigs)


# ---------- verifiable accounting ----------
async def test_reconcile_two_routes_and_detects_tampering(db):
    async with db.write() as s:
        w = await pf.create_wallet(s, "w", ["t"], D(100))
        f = walk([(D("0.40"), D(100))], D(10), None, "BUY", OPT, T, D("0.07"))
        await pf.record_buy(s, w, f, client_order_id="o1", group_id="g1", strategy="t", market_id="m1",
                            token_id="tk", outcome="Yes", scenario="OPTIMISTIC", latency_ms=0, book={}, signal={},
                            uncertainty=None, exit_rules=None)
        s.add(Market(id="m1", condition_id="c", question="q", resolved=True, payouts=["1", "0"]))
        await s.flush()
        await pf.settle_market(s, await s.get(Market, "m1"), {"tk": 0})
        rec = await pf.reconcile(s, w, lambda t: None)
        assert rec["checks_passed"], rec["problems"]
        assert rec["realized_pnl"] == pytest.approx(10 - 4 - float(f.fee))
        assert rec["total_equity"] == pytest.approx(100 + rec["realized_pnl"])
        assert rec["fees"] == pytest.approx(float(f.fee))
        # a duplicated settlement row (double counting) must be caught
        s.add(Position(wallet_id=w.id, group_id="g1", strategy="t", market_id="m1", token_id="tk", shares=D(10),
                       cost=D(0), entry_price=D("0.4"), status="closed", proceeds=D(10), realized_pnl=D(10),
                       exit_reason="resolution"))
        w.cash += D(10)
        await s.flush()
        bad = await pf.reconcile(s, w, lambda t: None)
        assert not bad["checks_passed"]
        assert any("shares mismatch" in p or "cash from movements" in p for p in bad["problems"])


# ---------- engine: opportunity vanishes, exchange book verification ----------
async def _engine(tmp_path, rest_asks):
    session.init_engine(f"sqlite+aiosqlite:///{tmp_path}/e.db")
    await session.create_all()
    eng = Engine()
    await eng._bootstrap()
    mv = _mv(datetime.now(timezone.utc) + timedelta(days=1))
    eng.state.markets = {"m": mv}
    for tok, ask in (("y", "0.47"), ("n", "0.50")):
        b = Book(tok)
        b.replace([], [{"price": ask, "size": "1000"}], None, "test")
        eng.state.books[tok] = b
    eng.state.feed.connected, eng.state.feed.last_message = True, time.time()

    async def fake_books(tokens):
        def lv(v):
            price, size = v if isinstance(v, tuple) else (v, "1000")
            return [{"price": price, "size": size}]
        return [{"asset_id": t, "bids": [], "asks": lv(rest_asks[t])} for t in tokens]
    eng.collector.client.books = fake_books
    return eng


async def _wait_orders(n, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        async with session.read() as s:
            rows = (await s.scalars(select(PaperOrder))).all()
        if len(rows) >= n:
            return rows
        await asyncio.sleep(0.1)
    return rows


def _cancel_tasks():
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task() and "guarded" in repr(t.get_coro()):
            t.cancel()


async def test_fill_uses_exchange_book_when_worse(tmp_path):
    # the exchange has only 6 YES shares at 0.47, while our WebSocket book shows 1000
    eng = await _engine(tmp_path, {"y": ("0.47", "6"), "n": "0.50"})
    await eng.scan()
    orders = await _wait_orders(4)
    yes = [o for o in orders if o.token_id == "y"]
    no = [o for o in orders if o.token_id == "n"]
    # REALISTIC takes 80% of displayed depth: 4.8 shares, a partial fill, and the NO leg is left unhedged
    assert yes and all(o.filled_shares == D("4.80") and o.status == "partial" for o in yes)
    assert all(o.filled_shares > D("4.80") for o in no)
    assert all("unhedged" in (o.uncertainty or "") for o in orders)
    async with session.read() as s:
        checks = (await s.scalars(select(ExecutionCheck))).all()
        assert {c.result for c in checks if c.kind == "rest_book"} >= {"ws_better", "match"}
        for w in (await s.scalars(select(Wallet))).all():
            assert (await pf.reconcile(s, w, eng.state.price_of))["checks_passed"]
    _cancel_tasks()
    await session.engine().dispose()


async def test_opportunity_vanishes_before_execution(tmp_path):
    # on the exchange the arbitrage is already gone: YES now costs 0.60, above the planned limit
    eng = await _engine(tmp_path, {"y": "0.60", "n": "0.50"})
    await eng.scan()
    await asyncio.sleep(1.5)
    async with session.read() as s:
        y_orders = (await s.scalars(select(PaperOrder).where(PaperOrder.token_id == "y"))).all()
        assert all(o.filled_shares == 0 for o in y_orders)  # nothing bought at a price that no longer exists
        decisions = (await s.scalars(select(Decision))).all()
        assert decisions  # the outcome is persisted, not only kept in memory
    _cancel_tasks()
    await session.engine().dispose()
