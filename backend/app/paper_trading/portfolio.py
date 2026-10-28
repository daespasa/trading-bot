"""Virtual wallet accounting. Every function takes an open write session; callers own the transaction.

Invariant (checked by `integrity`):  cash == initial - sum(cost of all positions) + sum(proceeds of closed)
and cash >= 0. Order idempotency comes from the unique `client_order_id`.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.database.models import EquitySnapshot, Market, PaperOrder, Position, Wallet
from app.paper_trading.execution import D0, Fill

DEFAULT_WALLETS = [
    ("Arbitrage Wallet", ["arb_yes_no", "arb_multi", "related_markets"]),
    ("Rule-Based Wallet", ["crypto_momentum", "mean_reversion"]),
    ("LightGBM Wallet", ["ai_lightgbm"]),
    ("Logistic Regression Wallet", ["ai_logistic"]),
    ("Combined Strategy Wallet", ["arb_yes_no", "arb_multi", "related_markets", "crypto_momentum",
                                  "mean_reversion", "ai_lightgbm", "ai_logistic"]),
]


class DuplicateOrder(Exception):
    pass


async def create_wallet(s, name: str, strategies: list[str], capital: Decimal, scenario: str = "REALISTIC",
                        experiment: str = "exp-1", **limits) -> Wallet:
    if capital <= 0:
        raise ValueError("capital must be positive")
    w = Wallet(name=name, strategies=strategies, initial_capital=capital, cash=capital, scenario=scenario,
               experiment=experiment, **limits)
    s.add(w)
    await s.flush()
    return w


async def order_exists(s, client_order_id: str) -> bool:
    return (await s.scalar(select(PaperOrder.id).where(PaperOrder.client_order_id == client_order_id))) is not None


async def record_buy(s, wallet: Wallet, fill: Fill, *, client_order_id: str, group_id: str, strategy: str,
                     market_id: str, token_id: str, outcome: str, scenario: str, latency_ms: int,
                     book: dict, signal: dict, uncertainty: str | None, exit_rules: dict | None) -> PaperOrder:
    if await order_exists(s, client_order_id):
        raise DuplicateOrder(client_order_id)
    cost = fill.notional + fill.fee
    status, reason = fill.status, None
    if status != "rejected" and cost > wallet.cash:
        status, reason = "rejected", f"insufficient cash: need {cost}, have {wallet.cash}"
    order = PaperOrder(
        client_order_id=client_order_id, wallet_id=wallet.id, strategy=strategy, group_id=group_id,
        market_id=market_id, token_id=token_id, outcome=outcome, side="BUY", requested_shares=fill.requested,
        filled_shares=fill.filled if status != "rejected" else D0, avg_price=fill.avg_price,
        notional=fill.notional if status != "rejected" else D0, fee=fill.fee if status != "rejected" else D0,
        slippage=fill.slippage, status=status, uncertainty=uncertainty,
        reason=reason or ("no liquidity within limit after latency" if status == "rejected" else None),
        scenario=scenario, latency_ms=latency_ms, fills=fill.fills, book=book, signal=signal)
    s.add(order)
    if status == "rejected":
        await s.flush()
        return order
    wallet.cash -= cost
    s.add(Position(wallet_id=wallet.id, group_id=group_id, strategy=strategy, market_id=market_id,
                   token_id=token_id, outcome=outcome, shares=fill.filled, cost=cost, fees=fill.fee,
                   entry_price=fill.avg_price, exit_rules=exit_rules))
    await s.flush()
    return order


async def record_sell(s, wallet: Wallet, pos: Position, fill: Fill, *, client_order_id: str, reason: str,
                      scenario: str, latency_ms: int, book: dict) -> PaperOrder:
    if await order_exists(s, client_order_id):
        raise DuplicateOrder(client_order_id)
    if fill.filled > pos.shares:
        raise ValueError("cannot sell more than held")
    order = PaperOrder(
        client_order_id=client_order_id, wallet_id=wallet.id, strategy=pos.strategy, group_id=pos.group_id,
        market_id=pos.market_id, token_id=pos.token_id, outcome=pos.outcome, side="SELL",
        requested_shares=fill.requested, filled_shares=fill.filled, avg_price=fill.avg_price,
        notional=fill.notional, fee=fill.fee, slippage=fill.slippage, status=fill.status, reason=reason,
        scenario=scenario, latency_ms=latency_ms, fills=fill.fills, book=book, signal={"exit": reason})
    s.add(order)
    if fill.filled > 0:
        _close_part(s, pos, fill.filled, fill.notional - fill.fee, fill.avg_price, reason, fill.fee)
        wallet.cash += fill.notional - fill.fee
    await s.flush()
    return order


def _close_part(s, pos: Position, shares: Decimal, proceeds: Decimal, price: Decimal, reason: str,
                exit_fee: Decimal = D0) -> None:
    """Close `shares` of `pos`. A partial close splits off a closed row and keeps the rest open."""
    now = datetime.now(timezone.utc)
    if shares >= pos.shares:
        pos.status, pos.closed_at, pos.exit_price = "closed", now, price
        pos.proceeds, pos.exit_reason = proceeds, reason
        pos.fees += exit_fee
        pos.realized_pnl = proceeds - pos.cost
        return
    part_cost = (pos.cost * shares / pos.shares)
    part_fees = (pos.fees * shares / pos.shares)
    s.add(Position(wallet_id=pos.wallet_id, group_id=pos.group_id, strategy=pos.strategy,
                   market_id=pos.market_id, token_id=pos.token_id, outcome=pos.outcome, shares=shares,
                   cost=part_cost, fees=part_fees + exit_fee, entry_price=pos.entry_price, status="closed",
                   opened_at=pos.opened_at, closed_at=now, exit_price=price, proceeds=proceeds,
                   realized_pnl=proceeds - part_cost, exit_reason=reason))
    pos.shares -= shares
    pos.cost -= part_cost
    pos.fees -= part_fees


async def settle_market(s, market: Market, token_index: dict[str, int]) -> int:
    """Pay out all open positions of a resolved market at the official payout per share. Idempotent:
    only open positions are touched, and they are closed in the same transaction."""
    if not market.resolved or not market.payouts:
        return 0
    positions = (await s.scalars(select(Position).where(Position.market_id == market.id,
                                                       Position.status == "open"))).all()
    for pos in positions:
        payout = Decimal(str(market.payouts[token_index[pos.token_id]]))
        proceeds = pos.shares * payout
        wallet = await s.get(Wallet, pos.wallet_id)
        wallet.cash += proceeds
        _close_part(s, pos, pos.shares, proceeds, payout, "resolution")
    await s.flush()
    return len(positions)


async def open_positions(s, wallet_id: int) -> list[Position]:
    return list((await s.scalars(select(Position).where(Position.wallet_id == wallet_id,
                                                       Position.status == "open"))).all())


def mark_value(positions, price_of) -> Decimal:
    """Liquidation value: shares x best bid (price_of returns Decimal or None -> valued at 0)."""
    total = D0
    for p in positions:
        px = price_of(p.token_id)
        total += p.shares * (px if px is not None else D0)
    return total


async def snapshot_equity(s, wallet: Wallet, price_of) -> EquitySnapshot:
    pv = mark_value(await open_positions(s, wallet.id), price_of)
    snap = EquitySnapshot(wallet_id=wallet.id, cash=wallet.cash, positions_value=pv, equity=wallet.cash + pv)
    s.add(snap)
    return snap


async def integrity(s, wallet: Wallet) -> list[str]:
    problems = []
    rows = (await s.execute(select(Position.cost, Position.proceeds).where(Position.wallet_id == wallet.id))).all()
    expected = wallet.initial_capital - sum((r.cost for r in rows), D0) + sum((r.proceeds or D0 for r in rows), D0)
    if expected != wallet.cash:
        problems.append(f"cash mismatch: ledger {expected} vs wallet {wallet.cash}")
    if wallet.cash < 0:
        problems.append(f"negative cash {wallet.cash}")
    dup = await s.scalar(select(func.count()).select_from(
        select(PaperOrder.client_order_id).group_by(PaperOrder.client_order_id)
        .having(func.count() > 1).subquery()))
    if dup:
        problems.append(f"{dup} duplicated client_order_id")
    return problems


async def reconcile(s, wallet: Wallet, price_of) -> dict:
    """Accounting statement rebuilt two independent ways, plus consistency checks.

    Route A (positions): cash = initial - sum(cost) + sum(proceeds of closed).
    Route B (cash movements): cash = initial - sum(buy notional + fee) + sum(sell notional - fee)
                                     + sum(settlement payouts).
    Identity: equity = cash + open value = initial + realized + unrealized.
    Orders are fill-and-kill, so nothing is ever reserved by a resting order (reserved = 0 by construction).
    """
    pos = (await s.scalars(select(Position).where(Position.wallet_id == wallet.id))).all()
    orders = (await s.scalars(select(PaperOrder).where(PaperOrder.wallet_id == wallet.id,
                                                      PaperOrder.filled_shares != "0"))).all()
    open_ = [p for p in pos if p.status == "open"]
    closed = [p for p in pos if p.status == "closed"]
    cost_open = sum((p.cost for p in open_), D0)
    value_open = mark_value(open_, price_of)
    realized = sum((p.realized_pnl or D0 for p in closed), D0)
    fees = sum((o.fee for o in orders), D0)
    settlements = sum((p.proceeds or D0 for p in closed if p.exit_reason == "resolution"), D0)
    cash_b = (wallet.initial_capital - sum((o.notional + o.fee for o in orders if o.side == "BUY"), D0)
              + sum((o.notional - o.fee for o in orders if o.side == "SELL"), D0) + settlements)
    equity = wallet.cash + value_open
    unrealized = value_open - cost_open
    problems = await integrity(s, wallet)
    if cash_b != wallet.cash:
        problems.append(f"cash from movements {cash_b} != wallet cash {wallet.cash}")
    if equity != wallet.initial_capital + realized + unrealized:
        problems.append("equity identity broken: equity != initial + realized + unrealized")
    # every share bought must be in exactly one position row (catches double settlement / duplicated rows)
    bought: dict = {}
    for o in orders:
        k = (o.group_id, o.token_id)
        bought[k] = bought.get(k, D0) + (o.filled_shares if o.side == "BUY" else D0)
    held: dict = {}
    for p in pos:
        k = (p.group_id, p.token_id)
        held[k] = held.get(k, D0) + p.shares
    for k, v in bought.items():
        if held.get(k, D0) != v:
            problems.append(f"shares mismatch for {k}: bought {v}, in positions {held.get(k, D0)}")
    return {
        "wallet_id": wallet.id, "wallet": wallet.name, "initial_capital": float(wallet.initial_capital),
        "available_balance": float(wallet.cash), "reserved_capital": 0.0, "position_cost_basis": float(cost_open),
        "open_value_bid": float(value_open), "realized_pnl": float(realized), "unrealized_pnl": float(unrealized),
        "total_equity": float(equity), "net_return": float((equity - wallet.initial_capital) / wallet.initial_capital),
        "fees": float(fees), "settlement_payouts": float(settlements), "open_positions": len(open_),
        "checks_passed": not problems, "problems": problems,
    }
