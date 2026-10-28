import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.analytics import backtest as bt
from app.analytics import metrics as an
from app.analytics import profitability as prof
from app.analytics import reports, research, validation
from app.core.config import settings
from app.database import session as db
from app.database.models import (BacktestRun, Decision, EquitySnapshot, ExecutionCheck, Market, MLModel, Opportunity,
                                 Outcome, PaperOrder, Position, Prediction, Report, Snapshot, StrategyConfig,
                                 SystemEvent, TrainingRun, Wallet)
from app.machine_learning.metrics import calibration_bins, prob_metrics
from app.paper_trading import portfolio as pf
from app.paper_trading.execution import SCENARIOS, configure_scenarios
from app.services.engine import engine
from app.strategies import REGISTRY

router = APIRouter(prefix="/api")


def _f(x):
    return float(x) if x is not None else None


@router.get("/health")
async def health():
    return engine.health()


# ---------- overview ----------
@router.get("/overview")
async def overview():
    async with db.read() as s:
        wallets = (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).all()
        sums = [await an.wallet_summary(s, w, engine.state.price_of) for w in wallets]
        since = datetime.now(timezone.utc) - timedelta(days=14)
        curves = {w.name: await an.equity_curve(s, w.id, since) for w in wallets}
        recent_orders = (await s.scalars(select(PaperOrder).where(PaperOrder.filled_shares > 0)
                                         .order_by(PaperOrder.id.desc()).limit(8))).all()
        recent_opps = (await s.scalars(select(Opportunity).order_by(Opportunity.last_seen.desc()).limit(8))).all()
    total_init = sum(x["initial_capital"] for x in sums) or 1
    tot = {
        "virtual_balance": sum(x["cash"] for x in sums), "total_equity": sum(x["equity"] for x in sums),
        "initial_capital": total_init, "net_pnl": sum(x["net_pnl"] for x in sums),
        "open_positions": sum(x["open_positions"] for x in sums),
        "closed_trades": sum(x["closed_trades"] for x in sums),
    }
    tot["roi"] = tot["net_pnl"] / total_init
    closed = [x for x in sums if x["closed_trades"]]
    tot["win_rate"] = (sum(x["win_rate"] * x["closed_trades"] for x in closed) / tot["closed_trades"]) if closed else None
    tot["max_drawdown"] = max((x["max_drawdown"] for x in sums), default=0)
    return {"totals": tot, "wallets": sums, "curves": curves,
            "active_strategies": sum(1 for c in engine.configs.values() if c.enabled),
            "ml_experimental": any(c.enabled and not c.config.get("require_validation", 1)
                                   for n, c in engine.configs.items() if n.startswith("ai_")),
            "markets_monitored": len(engine.state.markets), "health": engine.health(),
            "recent_orders": [_order(o) for o in recent_orders], "recent_opportunities": [_opp(o) for o in recent_opps],
            "notifications": list(engine.notifications)[:10]}


# ---------- markets ----------
def _market_row(m) -> dict:
    out = {"id": m.id, "question": m.question, "event_title": m.event_title, "category": m.category,
           "end_date": m.end_date.isoformat() if m.end_date else None, "neg_risk": m.neg_risk, "slug": m.slug,
           "fees_enabled": m.fees_enabled, "fee_rate": float(m.fee_rate), "outcomes": []}
    for tok, name in zip(m.tokens, m.outcomes):
        b = engine.state.books.get(tok)
        bb, ba = (b.best_bid, b.best_ask) if b else (None, None)
        out["outcomes"].append({"token_id": tok, "name": name, "bid": _f(bb), "ask": _f(ba),
                                "spread": _f(ba - bb) if bb is not None and ba is not None else None,
                                "bid_depth": _f(b.depth_usdc("bid", 5, m.tick)) if b else None,
                                "ask_depth": _f(b.depth_usdc("ask", 5, m.tick)) if b else None,
                                "age_s": b.age() if b and b.ts_received else None,
                                "last_trade": _f(b.last_trade) if b else None})
    return out


@router.get("/markets")
async def markets(search: str = "", category: str = ""):
    ids = list(engine.state.markets)
    async with db.read() as s:
        meta = {r.id: (r.volume_24h, r.liquidity) for r in (await s.execute(
            select(Market.id, Market.volume_24h, Market.liquidity).where(Market.id.in_(ids)))).all()}
    rows = []
    for m in engine.state.markets.values():
        if search and search.lower() not in (m.question + (m.event_title or "")).lower():
            continue
        if category and m.category != category:
            continue
        r = _market_row(m)
        r["volume_24h"], r["liquidity"] = meta.get(m.id, (0, 0))
        rows.append(r)
    return rows


@router.get("/markets/{market_id}")
async def market_detail(market_id: str, hours: int = 24):
    async with db.read() as s:
        m = await s.get(Market, market_id)
        if not m:
            raise HTTPException(404)
        outs = (await s.scalars(select(Outcome).where(Outcome.market_id == market_id).order_by(Outcome.idx))).all()
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        hist = {}
        for o in outs:
            rows = (await s.execute(select(Snapshot.ts_received, Snapshot.best_bid, Snapshot.best_ask).where(
                Snapshot.token_id == o.token_id, Snapshot.ts_received >= since).order_by(Snapshot.ts_received))).all()
            step = max(1, len(rows) // 400)
            hist[o.name] = [{"ts": r[0].isoformat(), "bid": r[1], "ask": r[2]} for r in rows[::step]]
    books = {o.name: engine.state.books[o.token_id].to_dict(10) for o in outs if o.token_id in engine.state.books}
    return {"id": m.id, "question": m.question, "description": m.description, "resolution_source": m.resolution_source,
            "event_title": m.event_title, "category": m.category, "end_date": m.end_date.isoformat() if m.end_date else None,
            "resolved": m.resolved, "payouts": m.payouts, "neg_risk": m.neg_risk, "fee_rate": float(m.fee_rate),
            "tick_size": float(m.tick_size), "min_size": float(m.min_size), "volume_24h": m.volume_24h,
            "liquidity": m.liquidity, "slug": m.slug, "outcomes": [o.name for o in outs], "books": books, "history": hist}


# ---------- opportunities ----------
def _opp(o: Opportunity) -> dict:
    return {"id": o.id, "strategy": o.strategy, "title": o.title, "market_id": o.market_id, "kind": o.kind,
            "guaranteed": o.guaranteed, "gross_edge": o.gross_edge, "costs": o.costs, "net_edge": o.net_edge,
            "liquidity": o.liquidity, "risk": o.risk, "status": o.status, "reason": o.reason,
            "first_seen": o.first_seen.isoformat(), "last_seen": o.last_seen.isoformat(), "seen_count": o.seen_count,
            "details": o.details.get("details", o.details) if isinstance(o.details, dict) else o.details,
            "legs": o.details.get("legs") if isinstance(o.details, dict) else None}


@router.get("/opportunities")
async def opportunities(strategy: str = "", status: str = "", limit: int = Query(200, le=2000), offset: int = 0):
    q = select(Opportunity)
    if strategy:
        q = q.where(Opportunity.strategy == strategy)
    if status:
        q = q.where(Opportunity.status == status)
    async with db.read() as s:
        total = await s.scalar(select(func.count()).select_from(q.subquery()))
        rows = (await s.scalars(q.order_by(Opportunity.last_seen.desc()).limit(limit).offset(offset))).all()
    return {"total": total, "items": [_opp(o) for o in rows], "decisions": list(engine.decisions)[:100]}


# ---------- strategies ----------
@router.get("/strategies")
async def strategies():
    since = datetime.now(timezone.utc) - timedelta(days=7)
    async with db.read() as s:
        counts = {r["strategy"]: r for r in await an.opportunity_counts(s, since)}
    return [{"name": n, "family": st.family, "description": st.description, "defaults": st.defaults,
             "enabled": engine.configs[n].enabled if n in engine.configs else False,
             "config": engine.configs[n].config if n in engine.configs else st.defaults,
             "opportunities_7d": counts.get(n, {})} for n, st in REGISTRY.items()]


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


@router.put("/strategies/{name}")
async def update_strategy(name: str, body: StrategyUpdate):
    if name not in REGISTRY:
        raise HTTPException(404)
    async with db.write() as s:
        row = await s.get(StrategyConfig, name)
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.config is not None:
            defaults = REGISTRY[name].defaults
            unknown = set(body.config) - set(defaults)
            if unknown:
                raise HTTPException(422, f"unknown keys: {sorted(unknown)}")
            for k, v in body.config.items():
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                    raise HTTPException(422, f"{k} must be a non-negative number")
            if body.config.get("trade_usdc", 0) > float(settings.max_trade_usdc) * 10:
                raise HTTPException(422, "trade_usdc too large")
            row.config = dict(row.config) | body.config
        row.updated_at = datetime.now(timezone.utc)
    await engine.reload_configs()
    return {"ok": True}


# ---------- wallets ----------
@router.get("/wallets")
async def wallets(include_archived: bool = False):
    async with db.read() as s:
        q = select(Wallet) if include_archived else select(Wallet).where(Wallet.status != "archived")
        return [await an.wallet_summary(s, w, engine.state.price_of) for w in (await s.scalars(q)).all()]


class WalletCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    capital: Decimal = Field(gt=0, le=1_000_000)
    strategies: list[str]
    scenario: str = "REALISTIC"
    max_trade_usdc: Decimal = Field(Decimal("5"), gt=0)
    max_open_positions: int = Field(5, ge=1, le=100)
    max_daily_loss: Decimal = Field(Decimal("10"), gt=0)


@router.post("/wallets")
async def create_wallet(body: WalletCreate):
    if body.scenario not in SCENARIOS or not set(body.strategies) <= set(REGISTRY):
        raise HTTPException(422, "unknown scenario or strategy")
    async with db.write() as s:
        exp = await s.scalar(select(Wallet.experiment).order_by(Wallet.id.desc()).limit(1)) or "exp-1"
        w = await pf.create_wallet(s, body.name, body.strategies, body.capital, body.scenario, exp,
                                   max_trade_usdc=body.max_trade_usdc, max_open_positions=body.max_open_positions,
                                   max_daily_loss=body.max_daily_loss)
        await pf.snapshot_equity(s, w, engine.state.price_of)
        return {"id": w.id}


@router.post("/wallets/{wallet_id}/{action}")
async def wallet_action(wallet_id: int, action: str):
    status = {"pause": "paused", "resume": "running"}.get(action)
    if not status:
        raise HTTPException(404)
    async with db.write() as s:
        w = await s.get(Wallet, wallet_id)
        if not w or w.status == "archived":
            raise HTTPException(404)
        w.status = status
    engine.dirty.add("wallets")
    return {"ok": True, "status": status}


class ResetBody(BaseModel):
    capital: Decimal = Field(Decimal("100"), gt=0, le=1_000_000)
    scenario: str = "REALISTIC"


@router.post("/experiments/reset")
async def reset_experiment(body: ResetBody):
    """Archive every wallet (history is kept) and start a new experiment with the five default wallets."""
    if body.scenario not in SCENARIOS:
        raise HTTPException(422)
    async with db.write() as s:
        n = (await s.scalar(select(func.count(func.distinct(Wallet.experiment))))) or 0
        exp = f"exp-{n + 1}"
        await s.execute(update(Wallet).where(Wallet.status != "archived").values(status="archived"))
        for name, strats in pf.DEFAULT_WALLETS:
            w = await pf.create_wallet(s, name, strats, body.capital, body.scenario, exp,
                                       max_trade_usdc=settings.max_trade_usdc, max_open_positions=settings.max_open_positions,
                                       max_daily_loss=settings.max_daily_loss)
            await pf.snapshot_equity(s, w, engine.state.price_of)
    engine.dirty.add("wallets")
    return {"ok": True, "experiment": exp}


@router.get("/wallets/{wallet_id}/positions")
async def positions(wallet_id: int, status: str = "open"):
    async with db.read() as s:
        rows = (await s.scalars(select(Position).where(Position.wallet_id == wallet_id, Position.status == status)
                                .order_by(Position.id.desc()).limit(500))).all()
        qs = {m.id: m.question for m in (await s.scalars(select(Market).where(
            Market.id.in_({p.market_id for p in rows})))).all()}
    return [{"id": p.id, "market_id": p.market_id, "question": qs.get(p.market_id, ""), "outcome": p.outcome,
             "strategy": p.strategy, "shares": float(p.shares), "cost": float(p.cost), "entry_price": float(p.entry_price),
             "mark": _f(engine.state.price_of(p.token_id)),
             "value": _f(p.shares * engine.state.price_of(p.token_id)) if engine.state.price_of(p.token_id) is not None else None,
             "fees": float(p.fees), "opened_at": p.opened_at.isoformat(),
             "closed_at": p.closed_at.isoformat() if p.closed_at else None, "exit_price": _f(p.exit_price),
             "realized_pnl": _f(p.realized_pnl), "exit_reason": p.exit_reason, "group_id": p.group_id} for p in rows]


# ---------- trades ----------
def _order(o: PaperOrder) -> dict:
    return {"id": o.id, "created_at": o.created_at.isoformat(), "wallet_id": o.wallet_id, "strategy": o.strategy,
            "market_id": o.market_id, "title": (o.signal or {}).get("title", ""), "outcome": o.outcome, "side": o.side,
            "requested_shares": float(o.requested_shares), "filled_shares": float(o.filled_shares),
            "avg_price": _f(o.avg_price), "notional": float(o.notional), "fee": float(o.fee),
            "slippage": float(o.slippage), "status": o.status, "uncertainty": o.uncertainty, "reason": o.reason,
            "scenario": o.scenario, "latency_ms": o.latency_ms, "group_id": o.group_id}


async def _trade_rows(wallet_id: int | None, strategy: str, limit: int, offset: int):
    q = select(PaperOrder)
    if wallet_id:
        q = q.where(PaperOrder.wallet_id == wallet_id)
    if strategy:
        q = q.where(PaperOrder.strategy == strategy)
    async with db.read() as s:
        total = await s.scalar(select(func.count()).select_from(q.subquery()))
        rows = (await s.scalars(q.order_by(PaperOrder.id.desc()).limit(limit).offset(offset))).all()
        pos = {(p.group_id, p.token_id): p for p in (await s.scalars(select(Position).where(
            Position.group_id.in_({o.group_id for o in rows})))).all()}
    out = []
    for o in rows:
        d = _order(o)
        p = pos.get((o.group_id, o.token_id))
        d["position_status"] = p.status if p else None
        d["exit_price"] = _f(p.exit_price) if p else None
        d["realized_pnl"] = _f(p.realized_pnl) if p and o.side == "BUY" else None
        out.append(d)
    return total, out


@router.get("/trades")
async def trades(wallet_id: int | None = None, strategy: str = "", limit: int = Query(200, le=5000), offset: int = 0):
    total, rows = await _trade_rows(wallet_id, strategy, limit, offset)
    return {"total": total, "items": rows}


def _cell(v):
    """Neutralise spreadsheet formulas (CSV injection): market titles come from third parties."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(rows: list[dict], name: str):
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: _cell(v) for k, v in r.items()} for r in rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@router.get("/trades.csv")
async def trades_csv(wallet_id: int | None = None, strategy: str = ""):
    _, rows = await _trade_rows(wallet_id, strategy, 100000, 0)
    return _csv(rows, "trades")


EXPORTS = {"decisions": Decision, "execution_checks": ExecutionCheck, "markets": Market, "outcomes": Outcome, "opportunities": Opportunity, "orders": PaperOrder,
           "positions": Position, "equity": EquitySnapshot, "predictions": Prediction, "models": MLModel,
           "snapshots": Snapshot, "events": SystemEvent, "wallets": Wallet}


@router.get("/export/{table}.csv")
async def export(table: str, days: int = 7):
    model = EXPORTS.get(table)
    if not model:
        raise HTTPException(404)
    q = select(model)
    if model is Snapshot:
        q = q.where(Snapshot.ts_received >= datetime.now(timezone.utc) - timedelta(days=days))
    async with db.read() as s:
        rows = (await s.scalars(q.limit(500000))).all()
    cols = [c.name for c in model.__table__.columns]
    return _csv([{c: getattr(r, c) for c in cols} for r in rows], table)


# ---------- analytics ----------
@router.get("/analytics")
async def analytics(days: int = 30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with db.read() as s:
        wallets = (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).all()
        out = []
        for w in wallets:
            curve = await an.equity_curve(s, w.id, since)
            groups = await an.closed_group_pnls(s, w.id)
            by_strat: dict = {}
            for strat, pnl, _ in groups.values():
                e = by_strat.setdefault(strat, {"strategy": strat, "pnl": 0.0, "trades": 0})
                e["pnl"] += pnl
                e["trades"] += 1
            fees = (await s.execute(select(PaperOrder.created_at, PaperOrder.fee, PaperOrder.slippage,
                                           PaperOrder.filled_shares).where(PaperOrder.wallet_id == w.id,
                                                                          PaperOrder.filled_shares > 0)
                                    .order_by(PaperOrder.created_at))).all()
            cum, cum_rows = 0.0, []
            for t, fee, slip, sh in fees:
                cum += float(fee) + max(0.0, float(slip * sh))
                cum_rows.append({"ts": t.isoformat(), "cumulative_costs": cum})
            out.append({"wallet": await an.wallet_summary(s, w, engine.state.price_of), "curve": curve,
                        "drawdown": an.drawdown_series(curve), "daily_pnl": an.daily_pnl(curve, float(w.initial_capital)),
                        "by_strategy": list(by_strat.values()), "trade_pnls": [p for _, p, _ in groups.values()],
                        "costs": cum_rows[:: max(1, len(cum_rows) // 300)],
                        "slippage_sensitivity": await an.slippage_sensitivity(s, w.id)})
        opp_counts = await an.opportunity_counts(s, since)
    return {"wallets": out, "opportunity_counts": opp_counts,
            "rejections": [{"key": k, "count": v} for k, v in sorted(engine.rejections.items(), key=lambda x: -x[1])]}


# ---------- ML ----------
def _model(m: MLModel) -> dict:
    return {"id": m.id, "kind": m.kind, "version": m.version, "feature_set": m.feature_set,
            "created_at": m.created_at.isoformat(), "status": m.status, "reason": m.reason, "n_train": m.n_train,
            "n_test": m.n_test, "metrics": m.metrics, "params": m.params}


@router.get("/ml/models")
async def ml_models(limit: int = 20):
    async with db.read() as s:
        rows = (await s.scalars(select(MLModel).order_by(MLModel.id.desc()).limit(limit))).all()
        runs = (await s.scalars(select(TrainingRun).order_by(TrainingRun.id.desc()).limit(10))).all()
    return {"models": [_model(m) for m in rows], "health": engine.health(),
            "runs": [{"id": r.id, "started_at": r.started_at.isoformat(), "status": r.status, "message": r.message,
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None, "dataset": r.dataset}
                     for r in runs]}


@router.post("/ml/train")
async def ml_train():
    return {"result": await engine.maybe_train(force=True)}


@router.get("/ml/predictions")
async def ml_predictions(limit: int = Query(200, le=2000)):
    async with db.read() as s:
        rows = (await s.scalars(select(Prediction).order_by(Prediction.id.desc()).limit(limit))).all()
    return [{"id": p.id, "ts": p.ts.isoformat(), "model_id": p.model_id, "model_kind": p.model_kind,
             "market_id": p.market_id, "question": p.question, "p_model": p.p_model, "p_market": p.p_market,
             "ask": p.ask, "edge": p.edge, "diff": p.p_model - p.p_market, "outcome": p.outcome} for p in rows]


@router.get("/ml/live-metrics")
async def ml_live_metrics():
    """Metrics on live predictions whose markets have since resolved (true out-of-sample, forward in time)."""
    async with db.read() as s:
        rows = (await s.execute(select(Prediction.model_id, Prediction.model_kind, Prediction.p_model,
                                       Prediction.p_market, Prediction.outcome)
                                .where(Prediction.outcome.in_([0.0, 1.0])))).all()
    by: dict = {}
    for r in rows:
        by.setdefault((r.model_id, r.model_kind), []).append(r)
    out = []
    for (mid, kind), rs in by.items():
        y = [r.outcome for r in rs]
        out.append({"model_id": mid, "kind": kind, "model": prob_metrics(y, [r.p_model for r in rs]),
                    "market": prob_metrics(y, [r.p_market for r in rs]),
                    "calibration": calibration_bins(y, [r.p_model for r in rs])})
    return out


# ---------- reports / backtests ----------
@router.get("/reports")
async def list_reports():
    async with db.read() as s:
        rows = (await s.scalars(select(Report).order_by(Report.period_start.desc()).limit(100))).all()
    return [{"id": r.id, "kind": r.kind, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
             "created_at": r.created_at.isoformat(), "data": r.data} for r in rows]


@router.get("/reports/{rid}.md", response_class=PlainTextResponse)
async def report_md(rid: int):
    async with db.read() as s:
        r = await s.get(Report, rid)
    if not r:
        raise HTTPException(404)
    return r.markdown


@router.post("/reports/preview")
async def report_preview(kind: str = "daily"):
    """Report for the current, unfinished period (not stored as the official one)."""
    now = datetime.now(timezone.utc)
    start = an.utc_day_start(now) if kind == "daily" else an.week_start(now)
    async with db.write() as s:
        r = await reports.build(s, f"{kind}-preview", start, now, engine.health()["uptime_s"])
        data, md = r.data, r.markdown
        await s.rollback()
    return {"data": data, "markdown": md}


class BacktestBody(BaseModel):
    start: datetime
    end: datetime
    strategies: list[str] = list(bt.REPLAYABLE)
    scenario: str = "REALISTIC"
    capital: float = 100
    trade_usdc: float = 5
    max_open_positions: int = 5
    configs: dict = {}


_bt_lock = asyncio.Lock()


@router.post("/backtests")
async def create_backtest(body: BacktestBody):
    if body.scenario not in SCENARIOS or body.end <= body.start:
        raise HTTPException(422)
    params = body.model_dump(mode="json")
    async with db.write() as s:
        run = BacktestRun(params=params)
        s.add(run)
        await s.flush()
        rid = run.id

    async def work():
        async with _bt_lock:  # one backtest at a time
            try:
                res, status = await asyncio.to_thread(bt.run, params), "done"
            except Exception as e:
                res, status = {"error": repr(e)}, "failed"
            async with db.write() as s:
                r = await s.get(BacktestRun, rid)
                r.results, r.status = res, status
            engine.dirty.add("backtests")

    asyncio.create_task(work())
    return {"id": rid}


@router.get("/backtests")
async def list_backtests():
    async with db.read() as s:
        rows = (await s.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(50))).all()
    return [{"id": r.id, "created_at": r.created_at.isoformat(), "status": r.status, "params": r.params,
             "results": r.results} for r in rows]


# ---------- system / settings ----------
@router.get("/system")
async def system():
    async with db.read() as s:
        events = (await s.scalars(select(SystemEvent).order_by(SystemEvent.id.desc()).limit(100))).all()
    return {"health": engine.health(), "resources": list(engine.resources),
            "events": [{"ts": e.ts.isoformat(), "level": e.level, "kind": e.kind, "message": e.message} for e in events]}


@router.get("/settings")
async def get_settings():
    return {"runtime": engine.runtime, "profile": settings.resource_profile, "max_markets_ceiling": settings.max_markets,
            "snapshot_seconds": settings.snapshot_seconds, "scan_seconds": settings.scan_seconds,
            "stale_seconds": settings.stale_seconds, "ml_threads": settings.ml_threads,
            "defaults": {"initial_capital": float(settings.initial_capital), "max_trade_usdc": float(settings.max_trade_usdc),
                         "max_open_positions": settings.max_open_positions, "max_daily_loss": float(settings.max_daily_loss)},
            "scenarios": {k: {"latency_ms": v.latency_ms, "depth_fraction": float(v.depth_fraction),
                              "extra_ticks": v.extra_ticks, "fee_mode": v.fee_mode, "edge_margin": float(v.edge_margin)}
                          for k, v in SCENARIOS.items()},
            "real_trading": False}


@router.put("/settings")
async def put_settings(body: dict):
    try:
        return {"runtime": engine.update_runtime(body)}
    except ValueError as e:
        raise HTTPException(422, str(e))


class WalletLimits(BaseModel):
    max_trade_usdc: Decimal | None = Field(None, gt=0)
    max_open_positions: int | None = Field(None, ge=1, le=100)
    max_daily_loss: Decimal | None = Field(None, gt=0)
    scenario: str | None = None


@router.put("/wallets/{wallet_id}")
async def update_wallet(wallet_id: int, body: WalletLimits):
    if body.scenario and body.scenario not in SCENARIOS:
        raise HTTPException(422)
    async with db.write() as s:
        w = await s.get(Wallet, wallet_id)
        if not w:
            raise HTTPException(404)
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(w, k, v)
    return {"ok": True}


# ---------- profitability center ----------
@router.get("/profitability")
async def profitability():
    async with db.read() as s:
        result = await prof.analyze(s, engine.state.price_of)
        wallets = (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).all()
        accounting = [await pf.reconcile(s, w, engine.state.price_of) for w in wallets]
        checks = await validation.check_summary(s)
        funnel = await validation.funnel(s)
        curves = {w.name: await an.equity_curve(s, w.id) for w in wallets}
    active_ids = {w.id for w in wallets}
    result["strategies"] = [r for r in result["strategies"] if r["wallet_id"] in active_ids]
    return result | {"accounting": accounting, "execution_checks": checks, "funnel": funnel, "equity_curves": curves,
                     "scenarios": {k: {"latency_ms": v.latency_ms, "depth_fraction": float(v.depth_fraction),
                                       "extra_ticks": v.extra_ticks, "fee_mode": v.fee_mode,
                                       "edge_margin": float(v.edge_margin)} for k, v in SCENARIOS.items()}}


@router.get("/profitability/trades")
async def profitability_trades(wallet_id: int, strategy: str):
    async with db.read() as s:
        return await prof.trades_for(s, engine.state.price_of, wallet_id, strategy)


@router.get("/orders/{order_id}/trace")
async def order_trace(order_id: int):
    """Everything known about one simulated order, stage by stage, with the data available at decision time."""
    async with db.read() as s:
        o = await s.get(PaperOrder, order_id)
        if not o:
            raise HTTPException(404)
        sig = o.signal or {}
        opp = await s.get(Opportunity, sig.get("opportunity_id")) if sig.get("opportunity_id") else None
        decisions = (await s.scalars(select(Decision).where(Decision.wallet_id == o.wallet_id,
                                                           Decision.opportunity_id == sig.get("opportunity_id")))).all()
        checks = (await s.scalars(select(ExecutionCheck).where(ExecutionCheck.order_id == o.id))).all()
        legs = (await s.scalars(select(PaperOrder).where(PaperOrder.group_id == o.group_id,
                                                        PaperOrder.wallet_id == o.wallet_id))).all()
        positions = (await s.scalars(select(Position).where(Position.group_id == o.group_id,
                                                           Position.wallet_id == o.wallet_id))).all()
        m = await s.get(Market, o.market_id)
    closed = [p for p in positions if p.status == "closed"]
    stages = [
        {"stage": "opportunity_detected", "at": opp.first_seen.isoformat() if opp else None,
         "info": {"strategy": o.strategy, "kind": sig.get("kind"), "gross_edge": sig.get("gross_edge"),
                  "net_edge": sig.get("net_edge"), "details": sig.get("details")}},
        {"stage": "opportunity_executable", "at": opp.first_seen.isoformat() if opp else None,
         "info": {"actionable": sig.get("actionable"), "reason": sig.get("reason"), "legs": sig.get("legs"),
                  "decision_books": sig.get("decision_books")}},
        {"stage": "paper_order_submitted", "at": o.created_at.isoformat(),
         "info": {"scenario": o.scenario, "latency_ms": o.latency_ms, "requested_shares": float(o.requested_shares),
                  "decisions": [{"decision": d.decision, "reason": d.reason, "count": d.count} for d in decisions]}},
        {"stage": "paper_order_filled", "at": o.created_at.isoformat(),
         "info": {"status": o.status, "filled_shares": float(o.filled_shares), "avg_price": _f(o.avg_price),
                  "fee": float(o.fee), "slippage_per_share": float(o.slippage), "fills": o.fills,
                  "book_after_latency": o.book, "uncertainty": o.uncertainty,
                  "checks": [{"kind": c.kind, "result": c.result, "details": c.details} for c in checks]}},
        {"stage": "position_opened", "at": min((p.opened_at for p in positions), default=None) and
         min(p.opened_at for p in positions).isoformat(),
         "info": {"legs": [{"order_id": x.id, "token_id": x.token_id, "outcome": x.outcome, "filled": float(x.filled_shares),
                            "avg_price": _f(x.avg_price)} for x in legs]}},
        {"stage": "position_closed", "at": max((p.closed_at for p in closed), default=None) and
         max(p.closed_at for p in closed).isoformat(),
         "info": {"closed": len(closed), "open": len(positions) - len(closed),
                  "exit_reasons": sorted({p.exit_reason for p in closed if p.exit_reason}),
                  "realized_pnl": float(sum((p.realized_pnl or 0 for p in closed), Decimal(0)))}},
        {"stage": "market_settled", "at": m.resolved_at.isoformat() if m and m.resolved_at else None,
         "info": {"resolved": bool(m and m.resolved), "payouts": m.payouts if m else None,
                  "end_date": m.end_date.isoformat() if m and m.end_date else None}},
    ]
    return {"order": _order(o), "question": m.question if m else "", "stages": stages}


@router.get("/research/opportunities")
async def research_opportunities():
    return {"criteria": list(research.CRITERIA), "items": research.ranked()}
