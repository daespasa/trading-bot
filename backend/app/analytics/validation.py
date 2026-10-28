"""Execution Validator: independent evidence on whether simulated fills could really have happened.

1. rest_book: at fill time the engine also fetches the exchange's own book over REST and walks it with the
   same scenario. The worse of (our WebSocket book, exchange book) is what the wallet gets; both are stored.
2. prints: after the fact, real trades on the same token (Polymarket Data API) around the fill time are
   compared with our price. Someone else buying at our price or better = corroborated; only worse prices =
   contradicted; no trades in the window = unverifiable.
Results live in `execution_checks`, never inside the original order rows.
"""
import asyncio
import logging
from decimal import Decimal

import httpx
from sqlalchemy import func, select

from app.database.models import Decision, ExecutionCheck, Market, Opportunity, PaperOrder, Position
from app.paper_trading.execution import Fill

log = logging.getLogger(__name__)
DATA_API = "https://data-api.polymarket.com"
PRINT_WINDOW_S = 30
REQUEST_GAP_S = 1.0  # the public Data API answers 429 to bursts; this job is not time-critical


def top(levels) -> str | None:
    return str(levels[0][0]) if levels else None


def compare_fills(ws: Fill, rest: Fill) -> str:
    """Classify our WebSocket-book fill against the exchange-book fill of the same order."""
    if ws.filled == rest.filled and ws.notional == rest.notional:
        return "match"
    ws_better = ws.filled > rest.filled or (ws.filled == rest.filled and ws.notional < rest.notional)
    return "ws_better" if ws_better else "ws_worse"


def worse_fill(ws: Fill, rest: Fill) -> Fill:
    """The conservative choice between two simulated fills of the same buy order."""
    if rest.filled < ws.filled:
        return rest
    if rest.filled == ws.filled and rest.notional > ws.notional:
        return rest
    return ws


def classify_prints(trades: list[dict], token_id: str, ts: float, price: Decimal, tick: Decimal) -> tuple[str, dict]:
    """trades: Data API rows. Our BUY at `price` is corroborated if a real taker BUY of the same token traded at
    <= price (+ half a tick tolerance) within +-PRINT_WINDOW_S seconds."""
    near = [t for t in trades if t.get("asset") == token_id and str(t.get("side")).upper() == "BUY"
            and abs(float(t.get("timestamp", 0)) - ts) <= PRINT_WINDOW_S]
    if not near:
        return "unverifiable", {"window_s": PRINT_WINDOW_S, "trades_in_window": 0}
    prices = sorted(Decimal(str(t["price"])) for t in near)
    ok = prices[0] <= price + tick / 2
    return ("corroborated" if ok else "contradicted"), {
        "window_s": PRINT_WINDOW_S, "trades_in_window": len(near), "best_real_price": str(prices[0]),
        "our_price": str(price)}


async def fetch_trades(http: httpx.AsyncClient, condition_id: str, since_ts: float, pages: int = 4) -> list[dict]:
    out: list[dict] = []
    for page in range(pages):
        r = await http.get(f"{DATA_API}/trades", params={"market": condition_id, "limit": 500, "offset": page * 500,
                                                          "takerOnly": "true"})
        r.raise_for_status()
        rows = r.json()
        out += [{k: t.get(k) for k in ("asset", "side", "price", "size", "timestamp")} for t in rows]  # no user data
        if len(rows) < 500 or min(float(t["timestamp"]) for t in rows) < since_ts:
            break
        await asyncio.sleep(REQUEST_GAP_S)
    return out


async def run_print_checks(db, http: httpx.AsyncClient, limit: int = 25) -> int:
    """Check filled BUY orders older than 2 minutes that have no `prints` check yet."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    async with db.read() as s:
        done = select(ExecutionCheck.order_id).where(ExecutionCheck.kind == "prints")
        rows = (await s.execute(
            select(PaperOrder, Market.condition_id, Market.tick_size).join(Market, Market.id == PaperOrder.market_id)
            .where(PaperOrder.side == "BUY", PaperOrder.filled_shares != "0", PaperOrder.created_at < cutoff,
                   PaperOrder.id.not_in(done)).order_by(PaperOrder.id).limit(limit))).all()
    n = 0
    cache: dict[str, list[dict]] = {}
    for o, cid, tick in rows:
        ts = o.created_at.timestamp()
        try:
            if cid not in cache:
                await asyncio.sleep(REQUEST_GAP_S)
                cache[cid] = await fetch_trades(http, cid, ts - PRINT_WINDOW_S)
            result, details = classify_prints(cache[cid], o.token_id, ts, o.avg_price, tick)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # rate limited: stop this run, the rest waits for the next one
                log.info("print checks rate limited; %d done this run", n)
                break
            log.warning("print check failed for order %s: %s", o.id, e)
            continue
        except Exception as e:  # network trouble: try again next run
            log.warning("print check failed for order %s: %s", o.id, e)
            continue
        async with db.write() as s:
            s.add(ExecutionCheck(order_id=o.id, kind="prints", result=result, details=details))
        n += 1
    return n


async def funnel(s, since=None) -> dict[str, dict]:
    """Per strategy: detected -> executable -> submitted -> filled -> opened -> closed -> settled."""
    out: dict[str, dict] = {}

    def bump(strategy, key, n):
        out.setdefault(strategy, {k: 0 for k in ("detected", "executable", "submitted", "filled", "opened",
                                                  "closed", "settled", "rejected_by_risk")})[key] += n

    q = select(Opportunity.strategy, Opportunity.status, func.count()).group_by(Opportunity.strategy, Opportunity.status)
    for strat, status, n in (await s.execute(q)).all():
        bump(strat, "detected", n)
        if status in ("detected", "executed"):
            bump(strat, "executable", n)
    for strat, dec, n in (await s.execute(select(Decision.strategy, Decision.decision, func.count())
                                          .group_by(Decision.strategy, Decision.decision))).all():
        if dec in ("executed", "no_fill"):
            bump(strat, "submitted", n)
        elif dec == "rejected":
            bump(strat, "rejected_by_risk", n)
    for strat, n in (await s.execute(select(PaperOrder.strategy, func.count(func.distinct(PaperOrder.group_id)))
                                     .where(PaperOrder.side == "BUY", PaperOrder.filled_shares != "0")
                                     .group_by(PaperOrder.strategy))).all():
        bump(strat, "filled", n)
    for strat, st, reason, n in (await s.execute(
            select(Position.strategy, Position.status, Position.exit_reason, func.count(func.distinct(Position.group_id)))
            .group_by(Position.strategy, Position.status, Position.exit_reason))).all():
        bump(strat, "opened", n)
        if st == "closed":
            bump(strat, "closed", n)
            if reason == "resolution":
                bump(strat, "settled", n)
    return out


async def check_summary(s) -> dict:
    rows = (await s.execute(select(PaperOrder.strategy, ExecutionCheck.kind, ExecutionCheck.result, func.count())
                            .join(PaperOrder, PaperOrder.id == ExecutionCheck.order_id)
                            .group_by(PaperOrder.strategy, ExecutionCheck.kind, ExecutionCheck.result))).all()
    out: dict = {}
    for strat, kind, result, n in rows:
        out.setdefault(strat, {}).setdefault(kind, {})[result] = n
    return out
