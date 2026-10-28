"""Performance analytics. PnL is judged per closed trade *group* (an arbitrage pair is one trade)."""
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
from sqlalchemy import func, select

from app.database.models import EquitySnapshot, Market, Opportunity, PaperOrder, Position, Wallet
from app.paper_trading.execution import D0, SCENARIOS, effective_fee_rate, walk


def max_drawdown(equity: list[float]) -> tuple[float, float]:
    """(absolute, fraction of peak)."""
    peak, mdd, mdd_pct = -math.inf, 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd, mdd_pct = max(mdd, peak - e), max(mdd_pct, (peak - e) / peak)
    return mdd, mdd_pct


def evidence(pnls: list[float], min_n: int = 30, seed: int = 7) -> dict:
    """Is mean trade PnL > 0? Bootstrap 95% CI of the mean. Profit is 'evidenced' only if n >= min_n and
    the CI lower bound is above zero. Few trades or a CI spanning zero is reported as such."""
    n = len(pnls)
    if n == 0:
        return {"n": 0, "verdict": "NO TRADES"}
    a = np.asarray(pnls, float)
    out = {"n": n, "mean": float(a.mean()), "total": float(a.sum())}
    if n >= 2:
        sd = float(a.std(ddof=1))
        out["sd"], out["t"] = sd, (float(a.mean()) / (sd / math.sqrt(n)) if sd > 0 else None)
        rng = np.random.default_rng(seed)
        boots = rng.choice(a, (2000, n), replace=True).mean(axis=1)
        out["ci95"] = [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))]
    if n < min_n:
        out["verdict"] = f"INSUFFICIENT EVIDENCE (n={n} < {min_n})"
    elif out["ci95"][0] > 0:
        out["verdict"] = "POSITIVE EDGE (95% CI > 0)"
    elif out["ci95"][1] < 0:
        out["verdict"] = "NEGATIVE EDGE (95% CI < 0)"
    else:
        out["verdict"] = "NOT DISTINGUISHABLE FROM ZERO"
    return out


async def closed_group_pnls(s, wallet_id: int, since: datetime | None = None, until: datetime | None = None) -> dict:
    """{group_id: (strategy, realized_pnl, closed_at)} for groups whose positions are all closed."""
    rows = (await s.execute(select(Position.group_id, Position.strategy, Position.status, Position.realized_pnl,
                                   Position.closed_at).where(Position.wallet_id == wallet_id))).all()
    groups: dict = defaultdict(lambda: [None, D0, None, True])
    for g, strat, st, pnl, closed in rows:
        e = groups[g]
        e[0] = strat
        if st != "closed":
            e[3] = False
        else:
            e[1] += pnl or D0
            e[2] = max(e[2], closed) if e[2] else closed
    out = {}
    for g, (strat, pnl, closed, done) in groups.items():
        if done and (since is None or closed >= since) and (until is None or closed < until):
            out[g] = (strat, float(pnl), closed)
    return out


async def wallet_summary(s, w: Wallet, price_of) -> dict:
    pos = (await s.scalars(select(Position).where(Position.wallet_id == w.id))).all()
    open_ = [p for p in pos if p.status == "open"]
    pv = sum((p.shares * (price_of(p.token_id) or D0) for p in open_), D0)
    equity = w.cash + pv
    realized = sum((p.realized_pnl or D0 for p in pos if p.status == "closed"), D0)
    fees = sum((p.fees for p in pos), D0)
    groups = await closed_group_pnls(s, w.id)
    wins = sum(1 for _, pnl, _ in groups.values() if pnl > 0)
    eq = [float(e) for e in (await s.scalars(select(EquitySnapshot.equity).where(EquitySnapshot.wallet_id == w.id)
                                             .order_by(EquitySnapshot.ts))).all()] + [float(equity)]
    mdd, mdd_pct = max_drawdown([float(w.initial_capital)] + eq)
    return {
        "id": w.id, "name": w.name, "status": w.status, "scenario": w.scenario, "strategies": w.strategies,
        "experiment": w.experiment, "initial_capital": float(w.initial_capital), "cash": float(w.cash),
        "committed": float(sum((p.cost for p in open_), D0)), "positions_value": float(pv), "equity": float(equity),
        "net_pnl": float(equity - w.initial_capital), "roi": float((equity - w.initial_capital) / w.initial_capital),
        "realized_pnl": float(realized), "unrealized_pnl": float(pv - sum((p.cost for p in open_), D0)),
        "fees": float(fees), "open_positions": len({p.group_id for p in open_}), "closed_trades": len(groups),
        "win_rate": wins / len(groups) if groups else None, "max_drawdown": mdd, "max_drawdown_pct": mdd_pct,
        "max_trade_usdc": float(w.max_trade_usdc), "max_open_positions": w.max_open_positions,
        "max_daily_loss": float(w.max_daily_loss), "created_at": w.created_at.isoformat(),
        "evidence": evidence([p for _, p, _ in groups.values()]),
    }


async def equity_curve(s, wallet_id: int, since: datetime | None = None) -> list[dict]:
    q = select(EquitySnapshot.ts, EquitySnapshot.equity, EquitySnapshot.cash).where(EquitySnapshot.wallet_id == wallet_id)
    if since:
        q = q.where(EquitySnapshot.ts >= since)
    rows = (await s.execute(q.order_by(EquitySnapshot.ts))).all()
    step = max(1, len(rows) // 500)  # cap points sent to the browser
    return [{"ts": r.ts.isoformat(), "equity": float(r.equity), "cash": float(r.cash)} for r in rows[::step]]


def drawdown_series(curve: list[dict]) -> list[dict]:
    peak, out = -math.inf, []
    for p in curve:
        peak = max(peak, p["equity"])
        out.append({"ts": p["ts"], "drawdown": p["equity"] - peak})
    return out


def daily_pnl(curve: list[dict], initial: float) -> list[dict]:
    last_by_day: dict[str, float] = {}
    for p in curve:
        last_by_day[p["ts"][:10]] = p["equity"]
    out, prev = [], initial
    for day in sorted(last_by_day):
        out.append({"day": day, "pnl": last_by_day[day] - prev})
        prev = last_by_day[day]
    return out


async def slippage_sensitivity(s, wallet_id: int) -> dict:
    """Re-price every closed position's entry on its recorded post-latency book under each scenario.
    Shows how much of the result depends on execution assumptions."""
    orders = (await s.execute(select(PaperOrder, Market.tick_size, Market.fee_rate, Market.fee_base_bps)
                              .join(Market, Market.id == PaperOrder.market_id)
                              .where(PaperOrder.wallet_id == wallet_id, PaperOrder.side == "BUY",
                                     PaperOrder.filled_shares > 0))).all()
    closed = {(p.group_id, p.token_id): p for p in (await s.scalars(
        select(Position).where(Position.wallet_id == wallet_id, Position.status == "closed"))).all()}
    out = {}
    for name, sc in SCENARIOS.items():
        total, n = D0, 0
        for o, tick, rate, bps in orders:
            p = closed.get((o.group_id, o.token_id))
            asks = [(Decimal(a), Decimal(b)) for a, b in (o.book or {}).get("asks", [])]
            if p is None or not asks:
                continue
            f = walk(asks, o.filled_shares, None, "BUY", sc, tick, effective_fee_rate(rate, bps, sc))
            if f.filled < o.filled_shares:  # this scenario would not have filled the whole order
                continue
            total += (p.proceeds or D0) - (f.notional + f.fee)
            n += 1
        out[name] = {"pnl": float(total), "orders": n}
    return out


async def opportunity_counts(s, since: datetime) -> list[dict]:
    rows = (await s.execute(select(Opportunity.strategy, Opportunity.status, func.count())
                            .where(Opportunity.last_seen >= since).group_by(Opportunity.strategy, Opportunity.status))).all()
    agg: dict = defaultdict(dict)
    for strat, st, n in rows:
        agg[strat][st] = n
    return [{"strategy": k, **v} for k, v in sorted(agg.items())]


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(now: datetime) -> datetime:
    d = utc_day_start(now)
    return d - timedelta(days=d.weekday())
