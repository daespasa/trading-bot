"""Common strategy interface. A strategy is a pure function of a `Context` (market state at time `now`)
and its config, returning `Signal`s. It never touches wallets: the engine applies risk + execution."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

from app.market_data.book import Book
from app.paper_trading.execution import (SCENARIOS, Scenario, buy_cost, effective_fee_rate, max_equal_shares,
                                         taker_fee)

D = Decimal
SHORT_HORIZON_STALE_S = 20


@dataclass
class Leg:
    market_id: str
    token_id: str
    outcome: str
    limit: Decimal  # worst acceptable price


@dataclass
class Signal:
    strategy: str
    key: str  # stable identity of the opportunity (dedupe + idempotent client order ids)
    title: str
    market_id: str
    event_id: str | None
    kind: str  # arbitrage | directional | speculative
    guaranteed: bool
    legs: list[Leg]
    mode: str  # equal_shares (all legs same size, payoff per set) | budget (single leg)
    payoff: Decimal  # guaranteed payoff per share-set (arbitrage) or 1 per share (directional)
    gross_edge: float  # per USDC, before costs
    costs: float  # per USDC: fees + depth slippage at planned size
    net_edge: float
    liquidity: float  # USDC available at acceptable prices
    risk: str
    actionable: bool = True  # False: logged as a discarded opportunity only
    reason: str | None = None
    min_net_edge: Decimal = D("0")
    exit_rules: dict | None = None
    details: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()} | {
            "legs": [{k: str(v) for k, v in asdict(leg).items()} for leg in self.legs]}


@dataclass
class Context:
    now: datetime
    markets: dict  # id -> MarketView
    books: dict[str, Book]
    events: dict  # event_id -> [MarketView]
    history: dict  # token -> deque[(epoch, mid)]
    crypto: object | None = None  # CryptoFeed
    refs: dict = field(default_factory=dict)  # (sym, open_ms) -> reference price, resolved by the engine
    predictions: dict = field(default_factory=dict)  # (model_kind, market_id) -> dict
    stale_seconds: float = 180
    scenario: Scenario = SCENARIOS["REALISTIC"]

    def fresh_book(self, token: str, m=None) -> Book | None:
        """Book if recent enough to trade on. Markets ending within 2 hours move fast, so their books must be
        younger than SHORT_HORIZON_STALE_S; a crossed book (bid >= ask) means missed updates and is rejected."""
        b = self.books.get(token)
        if b is None or not b.ts_received:
            return None
        limit = self.stale_seconds
        if m is not None and m.end_date and (m.end_date - self.now).total_seconds() < 7200:
            limit = min(limit, SHORT_HORIZON_STALE_S)
        if b.age(self.now.timestamp()) > limit:
            return None
        if b.best_bid is not None and b.best_ask is not None and b.best_bid >= b.best_ask:
            return None
        return b

    def hours_left(self, m) -> float | None:
        return (m.end_date - self.now).total_seconds() / 3600 if m.end_date else None


class Strategy:
    name = ""
    family = ""  # arbitrage | rule | ml
    description = ""
    defaults: dict = {}

    def evaluate(self, ctx: Context, cfg: dict) -> list[Signal]:
        raise NotImplementedError


def fee_rate(m, sc: Scenario) -> Decimal:
    return effective_fee_rate(m.fee_rate, m.fee_base_bps, sc)


def directional_signal(strategy: str, ctx: Context, m, idx: int, p_fair: float, cfg: dict, details: dict,
                       exit_rules: dict | None = None) -> Signal | None:
    """Buy outcome `idx` when fair probability beats the executable ask after fees by min_edge.
    Limit = highest price at which the edge still holds. Used by momentum and ML strategies."""
    b = ctx.fresh_book(m.tokens[idx], m)
    if b is None or b.best_ask is None:
        return None
    ask = b.best_ask
    rate = fee_rate(m, ctx.scenario)
    fee_ps = taker_fee(D(1), ask, rate)  # fee per share at the ask
    edge = D(str(p_fair)) - ask - fee_ps
    min_edge = D(str(cfg.get("min_edge", 0.03)))
    spread = (ask - b.best_bid) if b.best_bid is not None else D(1)
    limit = (D(str(p_fair)) - min_edge - fee_ps).quantize(m.tick, rounding="ROUND_DOWN")
    liquidity = sum((p * s for p, s in b.ask_levels() if p <= limit), D(0))
    reason = None
    if edge < min_edge:
        reason = f"edge {edge:.3f} < min {min_edge}"
    elif spread > D(str(cfg.get("max_spread", 0.05))):
        reason = f"spread {spread} too wide"
    elif liquidity < D(1):
        reason = "no liquidity under limit"
    if reason and edge <= 0:
        return None  # not an opportunity at all; don't log noise
    return Signal(
        strategy=strategy, key=f"{strategy}:{m.id}:{idx}", title=f"{m.question} -> {m.outcomes[idx]}",
        market_id=m.id, event_id=m.event_id, kind="directional", guaranteed=False,
        legs=[Leg(m.id, m.tokens[idx], m.outcomes[idx], limit)], mode="budget", payoff=D(1),
        gross_edge=float((D(str(p_fair)) - ask) / ask), costs=float(fee_ps / ask), net_edge=float(edge / ask),
        liquidity=float(liquidity), risk="high", actionable=reason is None, reason=reason,
        min_net_edge=min_edge, exit_rules=exit_rules,
        details=details | {"p_fair": round(p_fair, 4), "ask": str(ask), "fee_per_share": str(fee_ps),
                           "spread": str(spread)})


def arb_signal(strategy: str, ctx: Context, key: str, title: str, m, legs_tokens: list[tuple], payoff: Decimal,
               cfg: dict, guaranteed: bool, details: dict) -> Signal | None:
    """legs_tokens: [(MarketView, idx)]. Evaluates buying one share of each leg for `payoff`."""
    books = []
    for mv, idx in legs_tokens:
        b = ctx.fresh_book(mv.tokens[idx], mv)
        if b is None or b.best_ask is None:
            return None
        books.append(b)
    top_cost = sum((b.best_ask for b in books), D(0))
    if top_cost >= payoff:
        return None  # no apparent edge even at top of book
    sc = ctx.scenario
    budget = D(str(cfg.get("trade_usdc", 5)))
    min_edge = D(str(cfg.get("min_net_edge", 0.005)))
    legs = [(b.ask_levels(), mv.tick, fee_rate(mv, sc)) for b, (mv, _) in zip(books, legs_tokens)]
    q, cost = max_equal_shares(legs, budget, sc, payoff, min_edge, max(mv.min_size for mv, _ in legs_tokens))
    gross = float((payoff - top_cost) / top_cost)
    reason = None
    if q == 0:
        # evaluate the minimum size to report the real net edge
        qmin = max(mv.min_size for mv, _ in legs_tokens)
        c = [buy_cost(lv, qmin, sc, t, r) for lv, t, r in legs]
        if any(x is None for x in c):
            net, costs, reason = -1.0, 0.0, "insufficient depth for minimum order size"
        else:
            tot = sum(c, D(0))
            net = float((qmin * payoff - tot) / tot)
            costs = gross - net
            reason = f"net edge {net:.4f} below {min_edge} after fees/depth" if net < float(min_edge) \
                else "exceeds per-trade budget at minimum size"
    else:
        net = float((q * payoff - cost) / cost)
        costs = gross - net
    lim = []
    for b, (mv, idx) in zip(books, legs_tokens):
        # worst level we would touch for q shares (BASE assumptions), at least the top ask
        acc, worst = D(0), b.best_ask
        for p, s in b.ask_levels():
            if acc >= (q or mv.min_size):
                break
            acc += s * sc.depth_fraction
            worst = p
        lim.append(Leg(mv.id, mv.tokens[idx], mv.outcomes[idx], worst))
    liquidity = float(min(sum((p * s for p, s in b.ask_levels()[:5]), D(0)) for b in books))
    return Signal(strategy=strategy, key=key, title=title, market_id=m.id, event_id=m.event_id,
                  kind="arbitrage" if guaranteed else "speculative", guaranteed=guaranteed, legs=lim,
                  mode="equal_shares", payoff=payoff, gross_edge=gross, costs=costs, net_edge=net,
                  liquidity=liquidity, risk="low" if guaranteed else "high",
                  actionable=(q > 0 and guaranteed and reason is None), reason=reason or (None if guaranteed else "not provably risk-free"),
                  min_net_edge=min_edge,
                  details=details | {"top_cost": str(top_cost), "planned_shares": str(q), "planned_cost": str(cost),
                                     "asks": [str(b.best_ask) for b in books]})
