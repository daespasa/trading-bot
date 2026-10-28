"""Daily / weekly reports, stored in the DB and as Markdown under DATA_DIR/reports."""
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.analytics.metrics import closed_group_pnls, evidence, max_drawdown
from app.core.config import settings
from app.database.models import (EquitySnapshot, MLModel, Opportunity, PaperOrder, Prediction, Report, SystemEvent,
                                 Wallet)
from app.machine_learning.metrics import prob_metrics


async def _equity_at(s, wallet: Wallet, t: datetime) -> float:
    v = await s.scalar(select(EquitySnapshot.equity).where(EquitySnapshot.wallet_id == wallet.id, EquitySnapshot.ts <= t)
                       .order_by(EquitySnapshot.ts.desc()).limit(1))
    return float(v) if v is not None else float(wallet.initial_capital)


async def build(s, kind: str, start: datetime, end: datetime, uptime_s: float) -> Report:
    wallets = (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).all()
    ws = []
    for w in wallets:
        e0, e1 = await _equity_at(s, w, start), await _equity_at(s, w, end)
        curve = [float(x) for x in (await s.scalars(select(EquitySnapshot.equity).where(
            EquitySnapshot.wallet_id == w.id, EquitySnapshot.ts >= start, EquitySnapshot.ts < end)
            .order_by(EquitySnapshot.ts))).all()]
        trades = await s.scalar(select(func.count()).where(PaperOrder.wallet_id == w.id, PaperOrder.created_at >= start,
                                                             PaperOrder.created_at < end, PaperOrder.filled_shares > 0))
        fees = sum((o for o in (await s.scalars(select(PaperOrder.fee).where(
            PaperOrder.wallet_id == w.id, PaperOrder.created_at >= start, PaperOrder.created_at < end))).all()), Decimal(0))
        period = await closed_group_pnls(s, w.id, start, end)
        alltime = await closed_group_pnls(s, w.id)
        ws.append({"wallet": w.name, "start_equity": e0, "end_equity": e1, "net_pnl": e1 - e0,
                   "return": (e1 - e0) / e0 if e0 else None, "orders": trades, "fees": float(fees),
                   "max_drawdown": max_drawdown([e0] + curve + [e1])[0], "closed_trades": len(period),
                   "evidence_period": evidence([p for _, p, _ in period.values()]),
                   "evidence_all_time": evidence([p for _, p, _ in alltime.values()])})
    ranked = sorted(ws, key=lambda x: x["net_pnl"])
    opp = dict((await s.execute(select(Opportunity.status, func.count()).where(
        Opportunity.last_seen >= start, Opportunity.first_seen < end).group_by(Opportunity.status))).all())
    errors = (await s.execute(select(SystemEvent.kind, func.count()).where(
        SystemEvent.ts >= start, SystemEvent.ts < end, SystemEvent.level == "error").group_by(SystemEvent.kind))).all()
    ml = []
    for m in (await s.scalars(select(MLModel).order_by(MLModel.id.desc()).limit(6))).all():
        preds = (await s.execute(select(Prediction.p_model, Prediction.p_market, Prediction.outcome).where(
            Prediction.model_id == m.id, Prediction.outcome.in_([0.0, 1.0])))).all()
        live = {}
        if preds:
            y = [1.0 if r.outcome >= 0.5 else 0.0 for r in preds]
            live = {"model": prob_metrics(y, [r.p_model for r in preds]), "market": prob_metrics(y, [r.p_market for r in preds])}
        ml.append({"id": m.id, "kind": m.kind, "feature_set": m.feature_set, "status": m.status, "reason": m.reason,
                   "oos_model": m.metrics.get("model"), "oos_market": m.metrics.get("market"), "live_resolved": live})
    data = {"kind": kind, "start": start.isoformat(), "end": end.isoformat(), "wallets": ws,
            "best": ranked[-1]["wallet"] if ranked else None, "worst": ranked[0]["wallet"] if ranked else None,
            "opportunities": opp, "errors": [{"kind": k, "count": n} for k, n in errors], "ml": ml,
            "uptime_hours": uptime_s / 3600}
    rep = Report(kind=kind, period_start=start, period_end=end, data=data, markdown=to_markdown(data))
    s.add(rep)
    d = settings.data_dir / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{kind}-{start.date()}.md").write_text(rep.markdown)
    return rep


def to_markdown(d: dict) -> str:
    L = [f"# {d['kind'].title()} report {d['start'][:10]} → {d['end'][:10]}", "",
         "Paper trading only. Short-period gains are not evidence of an edge; see the evidence column.", "",
         "| Wallet | Start | End | Net PnL | Return | Orders | Fees | Max DD | Evidence (all time) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for w in d["wallets"]:
        r = f"{w['return']:.2%}" if w["return"] is not None else "-"
        L.append(f"| {w['wallet']} | {w['start_equity']:.2f} | {w['end_equity']:.2f} | {w['net_pnl']:+.2f} | {r} | "
                 f"{w['orders']} | {w['fees']:.4f} | {w['max_drawdown']:.2f} | {w['evidence_all_time']['verdict']} |")
    L += ["", f"Best: **{d['best']}** · Worst: **{d['worst']}** · Uptime: {d['uptime_hours']:.1f} h", "",
          f"Opportunities by status: {d['opportunities']}", f"Errors: {d['errors'] or 'none'}", "", "## Models", ""]
    for m in d["ml"]:
        L.append(f"- {m['kind']}/{m['feature_set']} #{m['id']}: **{m['status']}** — {m['reason']}")
    return "\n".join(L) + "\n"


async def due_periods(s, now: datetime) -> list[tuple[str, datetime, datetime]]:
    from app.analytics.metrics import utc_day_start, week_start
    out = []
    y0 = utc_day_start(now) - timedelta(days=1)
    first = await s.scalar(select(func.min(Wallet.created_at)))
    if first is None:
        return out
    if first < y0 + timedelta(days=1) and not await s.scalar(select(Report.id).where(Report.kind == "daily", Report.period_start == y0)):
        out.append(("daily", y0, y0 + timedelta(days=1)))
    w0 = week_start(now) - timedelta(days=7)
    if first < w0 + timedelta(days=7) and not await s.scalar(select(Report.id).where(Report.kind == "weekly", Report.period_start == w0)):
        out.append(("weekly", w0, w0 + timedelta(days=7)))
    return out
