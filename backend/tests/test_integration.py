"""API + engine integration. Market data here is SYNTHETIC (a hand-built book) and exists only in tests."""
import asyncio
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.database import session
from app.database.models import PaperOrder, Position, Wallet
from app.market_data.book import Book
from app.market_data.collector import MarketView
from app.paper_trading import portfolio as pf
from app.services.engine import Engine


@pytest.fixture(scope="module")
def client():
    if settings.db_path.exists():
        settings.db_path.unlink()
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")  # also exercises the migrations
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_smoke(client):
    h = client.get("/api/health").json()
    assert h["real_trading"] is False
    ws = client.get("/api/wallets").json()
    assert [w["name"] for w in ws][:2] == ["Arbitrage Wallet", "Rule-Based Wallet"]
    assert all(w["cash"] == 100 for w in ws)
    r = client.post("/api/wallets", json={"name": "t", "capital": "50", "strategies": ["arb_yes_no"]})
    assert r.status_code == 200
    wid = r.json()["id"]
    assert client.post(f"/api/wallets/{wid}/pause").json()["status"] == "paused"
    assert client.post(f"/api/wallets/{wid}/resume").json()["status"] == "running"
    assert client.post("/api/wallets", json={"name": "x", "capital": "-1", "strategies": []}).status_code == 422
    assert client.put("/api/strategies/arb_yes_no", json={"config": {"nope": 1}}).status_code == 422
    assert client.put("/api/strategies/arb_yes_no", json={"config": {"min_net_edge": 0.01}}).status_code == 200
    names = {s["name"] for s in client.get("/api/strategies").json()}
    assert {"arb_yes_no", "crypto_momentum", "ai_lightgbm"} <= names
    for path in ("/api/overview", "/api/markets", "/api/opportunities", "/api/trades", "/api/analytics",
                 "/api/ml/models", "/api/ml/predictions", "/api/ml/live-metrics", "/api/reports", "/api/settings",
                 "/api/system", "/api/backtests"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/trades.csv").status_code == 200
    # the Profitability Center and the wallet pages must agree on every wallet's numbers
    prof = client.get("/api/profitability").json()
    wallets = {w["id"]: w for w in client.get("/api/wallets").json()}
    for acc in prof["accounting"]:
        w = wallets[acc["wallet_id"]]
        assert acc["checks_passed"], acc["problems"]
        assert acc["total_equity"] == pytest.approx(w["equity"]) and acc["available_balance"] == pytest.approx(w["cash"])
        assert acc["realized_pnl"] == pytest.approx(w["realized_pnl"])
    assert client.get("/api/research/opportunities").json()["items"][0]["score"] is not None
    assert client.put("/api/settings", json={"scenarios": {"REALISTIC": {"depth_fraction": 9}}}).status_code == 422
    reset = client.post("/api/experiments/reset", json={"capital": "100"}).json()
    assert reset["experiment"].startswith("exp-")
    assert len(client.get("/api/wallets").json()) == 5


async def test_engine_executes_arbitrage_end_to_end(tmp_path):
    session.init_engine(f"sqlite+aiosqlite:///{tmp_path}/e.db")
    await session.create_all()
    eng = Engine()
    await eng._bootstrap()
    now = datetime.now(timezone.utc)
    mv = MarketView("m1", "Synthetic?", None, None, None, "other", False, False, now + timedelta(days=1), None,
                    False, D(0), 0, D("0.01"), D(5), True, False, "m1", "", ["y", "n"], ["Yes", "No"])
    eng.state.markets = {"m1": mv}
    for tok, ask in (("y", "0.47"), ("n", "0.50")):
        b = Book(tok)
        b.replace([], [{"price": ask, "size": "1000"}], None, "test")
        eng.state.books[tok] = b
    eng.state.feed.connected, eng.state.feed.last_message = True, time.time()

    async def offline_books(tokens):  # tests never touch the network
        raise TimeoutError("offline test")
    eng.collector.client.books = offline_books
    await eng.scan()
    orders = []
    for _ in range(40):  # BASE latency is 300 ms
        await asyncio.sleep(0.1)
        async with session.read() as s:
            orders = (await s.scalars(select(PaperOrder))).all()
        if len(orders) >= 4:
            break
    # Arbitrage and Combined wallets both trade both legs
    assert len(orders) == 4 and all(o.status == "filled" for o in orders)
    async with session.read() as s:
        for w in (await s.scalars(select(Wallet))).all():
            assert await pf.integrity(s, w) == []
            spent = w.initial_capital - w.cash
            assert spent <= w.max_trade_usdc
        pos = (await s.scalars(select(Position))).all()
        assert len(pos) == 4 and len({p.shares for p in pos}) == 1  # equal legs
    # the same opportunity is not traded twice
    eng.inflight.clear()
    await eng.scan()
    await asyncio.sleep(0.6)
    async with session.read() as s:
        assert len((await s.scalars(select(PaperOrder))).all()) == 4
    # feed loss halts new trades
    eng.state.feed.connected = False
    assert not eng.state.healthy()
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task() and "_execute_guarded" in repr(t.get_coro()):
            t.cancel()
    await session.engine().dispose()
