"""Profitability Analyzer: rigorous per-strategy results from the stored ledger.

Unit of analysis: a trade = one opportunity taken by one wallet (all its legs). Results are computed per
(wallet, strategy) and never summed across wallets, so a strategy shared by two wallets is not counted twice.
Dependence: trades sharing an event, or a crypto time window (BTC and ETH "Up or Down" for the same 5/15 min
window move together), form one cluster; confidence intervals resample clusters, not trades.
Market baseline: if the market mid at the moment of entry were the true probability, a trade's expected
PnL is shares*mid - cost. Realised PnL above that baseline is the part attributable to skill rather than luck.
"""
import math
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
from sqlalchemy import select

from app.database.models import (BacktestRun, Decision, ExecutionCheck, Market, MLModel, PaperOrder, Position,
                                 SystemEvent, Wallet)
from app.paper_trading.execution import D0, SCENARIOS, effective_fee_rate, walk

# Documented gate criteria (docs/PROFITABILITY_GATES.md). Minimums are necessary, never sufficient:
# the decision always rests on cluster-bootstrap confidence intervals and conservative execution.
GATES = {
    "min_clusters": 30, "min_days": 7,
    "ready_min_clusters": 100, "ready_min_days": 28, "ready_max_dd_pct": 0.25, "ready_min_positive_weeks": 3,
    "max_contradicted_share": 0.2,
}
STATUSES = ["RESEARCH", "BACKTESTING", "PAPER TESTING", "INSUFFICIENT DATA", "PROMISING", "REJECTED",
            "READY FOR HUMAN REVIEW"]


def cluster_key(m: Market | None, market_id: str) -> str:
    if m is not None and m.category == "crypto" and "updown" in (m.slug or "") and m.end_date:
        return f"window:{m.end_date.isoformat()}"
    if m is not None and m.event_id:
        return f"event:{m.event_id}"
    return f"market:{market_id}"


def cluster_bootstrap(cluster_pnls: list[float], reps: int = 4000, seed: int = 11) -> dict:
    n = len(cluster_pnls)
    if n == 0:
        return {"clusters": 0}
    a = np.asarray(cluster_pnls, float)
    out = {"clusters": n, "mean": float(a.mean()), "total": float(a.sum())}
    if n >= 2:
        boots = np.random.default_rng(seed).choice(a, (reps, n), replace=True).mean(axis=1)
        out["ci95"] = [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))]
        sd = float(a.std(ddof=1))
        out["t"] = float(a.mean() / (sd / math.sqrt(n))) if sd > 0 else None
    return out


def _book_levels(book: dict | None, side: str = "asks"):
    return [(Decimal(p), Decimal(s)) for p, s in (book or {}).get(side, [])]


def _mid(book: dict | None) -> Decimal | None:
    bids, asks = _book_levels(book, "bids"), _book_levels(book, "asks")
    if not bids or not asks:
        return None
    return (bids[0][0] + asks[0][0]) / 2


def reprice_entry(order: PaperOrder, m: Market, name: str) -> tuple[Decimal, Decimal] | None:
    """(shares filled, total cost incl. fees) for this entry under scenario `name`, or None if no book.
    OPTIMISTIC uses the decision-time book when recorded; the others use the book seen after latency."""
    sc = SCENARIOS[name]
    book = order.book
    if name == "OPTIMISTIC":
        book = ((order.signal or {}).get("decision_books") or {}).get(order.token_id) or order.book
    asks = _book_levels(book)
    if not asks:
        return None
    f = walk(asks, order.filled_shares, None, "BUY", sc, m.tick_size, effective_fee_rate(m.fee_rate, m.fee_base_bps, sc))
    return f.filled, f.notional + f.fee


async def _load(s):
    wallets = {w.id: w for w in (await s.scalars(select(Wallet))).all()}
    positions = (await s.scalars(select(Position))).all()
    orders = (await s.scalars(select(PaperOrder).where(PaperOrder.filled_shares != "0"))).all()
    mids = {p.market_id for p in positions} | {o.market_id for o in orders}
    markets = {m.id: m for m in (await s.scalars(select(Market).where(Market.id.in_(mids)))).all()}
    checks = defaultdict(dict)
    for c in (await s.scalars(select(ExecutionCheck))).all():
        checks[c.order_id][c.kind] = c.result
    return wallets, positions, orders, markets, checks


def build_trades(positions, orders, markets, checks, price_of) -> list[dict]:
    """One record per (wallet, group) with ledger, valuation, baseline, scenario re-pricing and checks."""
    by_group: dict[tuple, dict] = {}
    for p in positions:
        t = by_group.setdefault((p.wallet_id, p.group_id), {
            "wallet_id": p.wallet_id, "group_id": p.group_id, "strategy": p.strategy, "positions": [], "orders": []})
        t["positions"].append(p)
    for o in orders:
        t = by_group.get((o.wallet_id, o.group_id))
        if t is not None:
            t["orders"].append(o)
    out = []
    for t in by_group.values():
        ps, os_ = t["positions"], t["orders"]
        buys = [o for o in os_ if o.side == "BUY"]
        closed = all(p.status == "closed" for p in ps)
        cost = sum((p.cost for p in ps), D0)
        fees = sum((o.fee for o in os_), D0)
        proceeds = sum((p.proceeds or D0 for p in ps if p.status == "closed"), D0)
        realized = sum((p.realized_pnl or D0 for p in ps if p.status == "closed"), D0)
        open_ps = [p for p in ps if p.status == "open"]
        open_cost = sum((p.cost for p in open_ps), D0)
        bid_val = sum((p.shares * (price_of(p.token_id) or D0) for p in open_ps), D0)
        m0 = markets.get(ps[0].market_id)
        # market-implied expectation at entry
        ev = D0
        ev_known = True
        for o in buys:
            mid = _mid(o.book)
            if mid is None:
                ev_known = False
            else:
                ev += o.filled_shares * mid - (o.notional + o.fee)
        # scenario re-pricing of entries; exits keep their recorded proceeds/payouts, scaled on partial fills
        scen = {}
        for name in SCENARIOS:
            pnl, complete = D0, True
            for o in buys:
                m = markets.get(o.market_id)
                rp = reprice_entry(o, m, name) if m else None
                pos_for_leg = [p for p in ps if p.token_id == o.token_id]
                leg_proceeds = sum((p.proceeds or D0 for p in pos_for_leg if p.status == "closed"), D0)
                leg_open_val = sum((p.shares * (price_of(p.token_id) or D0) for p in pos_for_leg if p.status == "open"), D0)
                payout = leg_proceeds + leg_open_val
                if rp is None:
                    pnl += payout - (o.notional + o.fee)
                    complete = False
                    continue
                filled, c = rp
                frac = filled / o.filled_shares if o.filled_shares else D0
                if frac < 1:
                    complete = False
                pnl += payout * frac - c
            scen[name] = {"pnl": float(pnl), "complete": complete}
        ck = defaultdict(int)
        for o in buys:
            for kind, res in checks.get(o.id, {}).items():
                ck[f"{kind}:{res}"] += 1
        opened = min(p.opened_at for p in ps)
        closed_at = max((p.closed_at for p in ps if p.closed_at), default=None)
        out.append({
            "wallet_id": t["wallet_id"], "group_id": t["group_id"], "strategy": t["strategy"], "closed": closed,
            "opened_at": opened, "closed_at": closed_at if closed else None,
            "cluster": cluster_key(m0, ps[0].market_id), "market_id": ps[0].market_id,
            "question": m0.question if m0 else "", "order_ids": [o.id for o in buys], "cost": float(cost), "fees": float(fees),
            "proceeds": float(proceeds), "realized": float(realized), "open_cost": float(open_cost),
            "unrealized_bid": float(bid_val - open_cost) if open_ps else 0.0,
            "market_ev": float(ev) if ev_known else None, "scenarios": scen, "checks": dict(ck),
            "duration_min": ((closed_at - opened).total_seconds() / 60) if closed and closed_at else None,
        })
    return out


def _exposure(trades: list[dict], now: datetime) -> tuple[float, float, list[dict]]:
    """Max concurrent cost, time-weighted average cost, and the exposure series."""
    ev = []
    for t in trades:
        ev.append((t["opened_at"], t["cost"]))
        ev.append((t["closed_at"] or now, -t["cost"]))
    ev.sort(key=lambda x: x[0])
    cur = peak = area = 0.0
    series, last = [], ev[0][0] if ev else now
    for ts, d in ev:
        area += cur * (ts - last).total_seconds()
        cur += d
        last = ts
        peak = max(peak, cur)
        series.append({"ts": ts.isoformat(), "exposure": round(cur, 6)})
    span = (now - ev[0][0]).total_seconds() if ev else 0
    return peak, (area / span if span > 0 else 0.0), series[:: max(1, len(series) // 300)]


def metrics(trades: list[dict], capital: float | None, now: datetime) -> dict:
    closed = sorted([t for t in trades if t["closed"]], key=lambda t: t["closed_at"])
    opened = [t for t in trades if not t["closed"]]
    pnls = [t["realized"] for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    fees = sum(t["fees"] for t in trades)
    net = sum(pnls)
    gross = net + sum(t["fees"] for t in closed)
    by_cluster = defaultdict(float)
    for t in closed:
        by_cluster[t["cluster"]] += t["realized"]
    stats = cluster_bootstrap(list(by_cluster.values()))
    first = min((t["opened_at"] for t in trades), default=None)
    days = (now - first).total_seconds() / 86400 if first else 0.0
    # equity on realised PnL (open positions excluded on purpose: they are not results yet)
    eq, curve, peak, dd, dd_pct = capital or 0.0, [], capital or 0.0, 0.0, 0.0
    cum_fees, fee_curve = 0.0, []
    for t in closed:
        eq += t["realized"]
        cum_fees += t["fees"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        if peak > 0:
            dd_pct = max(dd_pct, (peak - eq) / peak)
        curve.append({"ts": t["closed_at"].isoformat(), "equity": eq, "pnl": eq - (capital or 0.0),
                      "drawdown": eq - peak})
        fee_curve.append({"ts": t["closed_at"].isoformat(), "fees": cum_fees})
    max_exp, avg_cap, exposure = _exposure(trades, now) if trades else (0.0, 0.0, [])
    # stability: halves by closing time (clusters), daily and weekly buckets
    halves = None
    if len(closed) >= 4:
        mid = len(closed) // 2
        halves = [sum(t["realized"] for t in closed[:mid]), sum(t["realized"] for t in closed[mid:])]
    daily, weekly = defaultdict(float), defaultdict(float)
    for t in closed:
        daily[t["closed_at"].date().isoformat()] += t["realized"]
        weekly[t["closed_at"].strftime("%G-W%V")] += t["realized"]
    scen = {name: sum(t["scenarios"][name]["pnl"] for t in closed) for name in SCENARIOS}
    scen_incomplete = {name: sum(1 for t in closed if not t["scenarios"][name]["complete"]) for name in SCENARIOS}
    baseline = [t["market_ev"] for t in closed if t["market_ev"] is not None]
    deployed = sum(t["cost"] for t in closed)
    annualized, ann_reason = None, None
    if capital and days >= 30 and stats["clusters"] >= GATES["min_clusters"]:
        annualized = (1 + net / capital) ** (365 / days) - 1
    else:
        ann_reason = "not annualised: needs 30+ days and enough independent trades"
    checks = defaultdict(int)
    for t in trades:
        for k, v in t["checks"].items():
            checks[k] += v
    prints_total = sum(v for k, v in checks.items() if k.startswith("prints:") and not k.endswith("unverifiable"))
    contradicted = checks.get("prints:contradicted", 0)
    return {
        "trades": len(closed), "open_trades": len(opened), "clusters": stats["clusters"], "days": days,
        "gross_pnl": gross, "net_pnl": net, "fees": fees, "realized_pnl": net,
        "unrealized_pnl_bid": sum(t["unrealized_bid"] for t in opened), "open_cost": sum(t["open_cost"] for t in opened),
        "roi_on_deployed": net / deployed if deployed else None, "roi_on_capital": net / capital if capital else None,
        "annualized": annualized, "annualized_note": ann_reason,
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "expectancy": net / len(pnls) if pnls else None,
        "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
        "max_drawdown": dd, "max_drawdown_pct": dd_pct, "max_exposure": max_exp, "avg_capital_used": avg_cap,
        "avg_duration_min": (sum(t["duration_min"] for t in closed if t["duration_min"]) / len(closed)) if closed else None,
        "stats": stats, "halves": halves, "daily_pnl": dict(daily), "weekly_pnl": dict(weekly),
        "positive_days": sum(1 for v in daily.values() if v > 0), "days_with_trades": len(daily),
        "scenarios": scen, "scenario_incomplete_trades": scen_incomplete,
        "cost_sensitivity": {"fees_x0": gross, "fees_x1": net, "fees_x2": net - sum(t["fees"] for t in closed),
                             "breakeven_fee_multiple": (gross / (gross - net)) if gross - net > 0 else None},
        "market_baseline_ev": sum(baseline) if baseline else None,
        "excess_over_market": (sum(t["realized"] for t in closed if t["market_ev"] is not None) - sum(baseline))
        if baseline else None,
        "checks": dict(checks), "contradicted_share": (contradicted / prints_total) if prints_total else None,
        "curve": curve[:: max(1, len(curve) // 300)], "fee_curve": fee_curve[:: max(1, len(fee_curve) // 300)],
        "exposure": exposure,
    }


def gate(m: dict, has_backtest: bool) -> tuple[str, list[str]]:
    """Status + the reasons behind it (documented in docs/PROFITABILITY_GATES.md)."""
    g = GATES
    if m["trades"] == 0 and m["open_trades"] == 0:
        return ("BACKTESTING" if has_backtest else "RESEARCH"), ["no paper trades yet"]
    if m["trades"] == 0 or m["clusters"] < g["min_clusters"] or m["days"] < g["min_days"]:
        return "INSUFFICIENT DATA", [f"{m['clusters']} independent trades over {m['days']:.1f} days; "
                                     f"needs at least {g['min_clusters']} over {g['min_days']} days before judging"]
    ci = m["stats"].get("ci95") or [0, 0]
    cons = m["scenarios"].get("CONSERVATIVE", 0)
    if ci[1] < 0 or (m["net_pnl"] <= 0 and cons < 0):
        return "REJECTED", [f"95% CI of mean PnL per independent trade [{ci[0]:.3f}, {ci[1]:.3f}]",
                            f"conservative-execution PnL {cons:+.2f}"]
    reasons, ok = [], True
    for cond, txt in (
        (ci[0] > 0, f"CI lower bound {ci[0]:.3f} > 0"),
        (cons > 0, f"conservative PnL {cons:+.2f} > 0"),
        (bool(m["halves"]) and min(m["halves"]) > 0, "profitable in both halves of the sample"),
        ((m["contradicted_share"] or 0) <= g["max_contradicted_share"], "fills not contradicted by real trades"),
    ):
        reasons.append(("✓ " if cond else "✗ ") + txt)
        ok &= cond
    if not ok:
        return "PAPER TESTING", reasons
    weeks = list(m["weekly_pnl"].values())[-4:]
    ready = (m["clusters"] >= g["ready_min_clusters"] and m["days"] >= g["ready_min_days"]
             and m["max_drawdown_pct"] <= g["ready_max_dd_pct"]
             and sum(1 for w in weeks if w > 0) >= g["ready_min_positive_weeks"]
             and (m["excess_over_market"] or 0) > 0)
    if ready:
        return "READY FOR HUMAN REVIEW", reasons + ["✓ long sample, bounded drawdown, consistent weeks, beats market baseline",
                                                    "human review required; this never enables real trading"]
    return "PROMISING", reasons + [f"needs {g['ready_min_clusters']}+ independent trades over {g['ready_min_days']}+ days "
                                   "with bounded drawdown before human review"]


async def analyze(s, price_of, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    wallets, positions, orders, markets, checks = await _load(s)
    trades = build_trades(positions, orders, markets, checks, price_of)
    backtested = set()
    for b in (await s.scalars(select(BacktestRun).where(BacktestRun.status == "done"))).all():
        backtested |= {k for k, v in (b.results or {}).get("strategies", {}).items() if v.get("signals")}
    model_status = {}
    for mdl in (await s.scalars(select(MLModel).where(MLModel.feature_set == "core").order_by(MLModel.id))).all():
        model_status[f"ai_{mdl.kind}"] = mdl.status
    dec = defaultdict(lambda: defaultdict(int))
    reasons = defaultdict(lambda: defaultdict(int))
    for d in (await s.scalars(select(Decision))).all():
        dec[(d.wallet_id, d.strategy)][d.decision] += 1
        if d.decision != "executed":
            reasons[(d.wallet_id, d.strategy)][d.reason.split(":")[0][:80]] += d.count
    grouped = _group(trades)
    traded = defaultdict(set)
    for wid, strat in grouped:
        traded[wid].add(strat)
    rows = []
    for (wid, strat), ts in grouped.items():
        w = wallets[wid]
        # Each trade belongs to exactly one strategy, so nothing is double counted inside a wallet. Return on the
        # wallet's capital is only meaningful when this strategy is the only one that has traded in it.
        sole = traded[wid] == {strat}
        m = metrics(ts, float(w.initial_capital) if sole else None, now)
        d = dec[(wid, strat)]
        submitted = d.get("executed", 0) + d.get("no_fill", 0)
        m["execution_ratio"] = (d.get("executed", 0) / submitted) if submitted else None
        m["decisions"] = dict(d)
        m["rejection_reasons"] = dict(sorted(reasons[(wid, strat)].items(), key=lambda x: -x[1])[:8])
        status, why = gate(m, strat in backtested)
        rows.append({"wallet_id": wid, "wallet": w.name, "wallet_status": w.status, "strategy": strat,
                     "sole_strategy_in_wallet": sole, "status": status, "status_reasons": why,
                     "model_status": model_status.get(strat), **m})
    return {"strategies": rows, "gates": GATES, "statuses": STATUSES, "generated_at": now.isoformat()}


def _group(trades: list[dict]) -> dict:
    g = defaultdict(list)
    for t in trades:
        g[(t["wallet_id"], t["strategy"])].append(t)
    return g


async def trades_for(s, price_of, wallet_id: int, strategy: str) -> list[dict]:
    wallets, positions, orders, markets, checks = await _load(s)
    ts = [t for t in build_trades(positions, orders, markets, checks, price_of)
          if t["wallet_id"] == wallet_id and t["strategy"] == strategy]
    for t in ts:
        t["opened_at"] = t["opened_at"].isoformat()
        t["closed_at"] = t["closed_at"].isoformat() if t["closed_at"] else None
    return sorted(ts, key=lambda t: t["opened_at"], reverse=True)


async def gate_changes(s, rows: list[dict]) -> list[SystemEvent]:
    """SystemEvents for strategies whose status changed since the last recorded gate event (audit trail)."""
    last = {}
    for e in (await s.scalars(select(SystemEvent).where(SystemEvent.kind == "gate").order_by(SystemEvent.id))).all():
        last[(e.data.get("wallet_id"), e.data.get("strategy"))] = e.data.get("status")
    out = []
    for r in rows:
        k = (r["wallet_id"], r["strategy"])
        if last.get(k) != r["status"]:
            out.append(SystemEvent(level="info", kind="gate", message=f"{r['wallet']} / {r['strategy']}: {last.get(k)} -> {r['status']}",
                                   data={"wallet_id": r["wallet_id"], "strategy": r["strategy"], "status": r["status"],
                                         "reasons": r["status_reasons"]}))
    return out
