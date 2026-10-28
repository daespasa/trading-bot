"""Pure execution maths: fees, book walking, scenario assumptions. No I/O, fully unit-tested.

Assumptions per scenario (documented in docs/EXECUTION_MODEL.md):
- latency_ms: delay between signal and order arrival. The engine re-reads the live book after it.
- depth_fraction: share of displayed depth we assume we can take (others compete for the same liquidity).
- extra_ticks: adverse price moves beyond what the book shows (stale snapshot, hidden cancellations).
- fee_mode: "documented" uses the category taker rate from the fee docs; "max" also considers the
  token's CLOB base_fee (bps) and takes the larger.
- edge_margin: extra net edge (per USDC) required on top of the strategy's own threshold before trading.
Scenario parameters can be overridden at runtime (Settings) through `configure_scenarios`.
"BASE" is the historical name of REALISTIC and is accepted as an alias.
All orders are marketable limit orders with fill-and-kill semantics: fill what is available up to the
limit price, cancel the rest. Buys use asks, sells use bids, never the mid.
"""
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

D0 = Decimal(0)
D1 = Decimal(1)
SHARE_Q = Decimal("0.01")
FEE_Q = Decimal("0.00001")

# Taker fee rates by category, docs.polymarket.com/trading/fees (checked 2026-10-28). Makers pay 0.
CATEGORY_FEE_RATES = {
    "crypto": Decimal("0.07"), "sports": Decimal("0.05"), "finance": Decimal("0.04"),
    "politics": Decimal("0.04"), "economics": Decimal("0.05"), "culture": Decimal("0.05"),
    "weather": Decimal("0.05"), "mentions": Decimal("0.04"), "tech": Decimal("0.04"),
    "geopolitics": D0, "other": Decimal("0.05"),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    latency_ms: int
    depth_fraction: Decimal
    extra_ticks: int
    fee_mode: str
    edge_margin: Decimal = Decimal("0")


DEFAULT_SCENARIOS = {
    "OPTIMISTIC": Scenario("OPTIMISTIC", 0, Decimal("1.0"), 0, "documented"),
    "REALISTIC": Scenario("REALISTIC", 300, Decimal("0.8"), 0, "documented"),
    "CONSERVATIVE": Scenario("CONSERVATIVE", 1000, Decimal("0.5"), 1, "max", Decimal("0.01")),
}
SCENARIOS = dict(DEFAULT_SCENARIOS)


def scenario(name: str) -> Scenario:
    return SCENARIOS["REALISTIC" if name == "BASE" else name]


def configure_scenarios(overrides: dict) -> None:
    """Apply {name: {latency_ms, depth_fraction, extra_ticks, fee_mode, edge_margin}} on top of the defaults."""
    for name, base in DEFAULT_SCENARIOS.items():
        o = (overrides or {}).get(name, {})
        lat = int(o.get("latency_ms", base.latency_ms))
        depth = Decimal(str(o.get("depth_fraction", base.depth_fraction)))
        ticks = int(o.get("extra_ticks", base.extra_ticks))
        fee_mode = o.get("fee_mode", base.fee_mode)
        margin = Decimal(str(o.get("edge_margin", base.edge_margin)))
        if not (0 <= lat <= 10000 and Decimal("0.05") <= depth <= 1 and 0 <= ticks <= 10
                and fee_mode in ("documented", "max") and 0 <= margin <= Decimal("0.2")):
            raise ValueError(f"invalid parameters for {name}")
        SCENARIOS[name] = Scenario(name, lat, depth, ticks, fee_mode, margin)


def fee_rate_for(category: str, fees_enabled: bool) -> Decimal:
    if not fees_enabled:
        return D0
    return CATEGORY_FEE_RATES.get(category, CATEGORY_FEE_RATES["other"])


def effective_fee_rate(documented: Decimal, base_bps: int, scenario: Scenario) -> Decimal:
    if scenario.fee_mode == "max" and documented > 0:
        return max(documented, Decimal(base_bps) / Decimal(10000))
    return documented


def taker_fee(shares: Decimal, price: Decimal, rate: Decimal) -> Decimal:
    """fee = C * feeRate * p * (1 - p), rounded to 5 dp, minimum 0.00001 when non-zero."""
    if rate <= 0 or shares <= 0:
        return D0
    fee = (shares * rate * price * (D1 - price)).quantize(FEE_Q, ROUND_HALF_UP)
    return max(fee, FEE_Q)


@dataclass
class Fill:
    side: str
    requested: Decimal
    filled: Decimal = D0
    notional: Decimal = D0  # sum(price*size), excl. fee
    fee: Decimal = D0
    fills: list = field(default_factory=list)
    top_price: Decimal | None = None  # best price at signal time, for slippage

    @property
    def avg_price(self) -> Decimal | None:
        return (self.notional / self.filled) if self.filled > 0 else None

    @property
    def status(self) -> str:
        if self.filled <= 0:
            return "rejected"
        return "filled" if self.filled >= self.requested else "partial"

    @property
    def slippage(self) -> Decimal:
        """Per-share adverse difference vs the top of book seen at signal time (>0 = worse)."""
        if self.avg_price is None or self.top_price is None:
            return D0
        diff = self.avg_price - self.top_price
        return diff if self.side == "BUY" else -diff

    @property
    def cash_delta(self) -> Decimal:
        """Cash change for the wallet: buys pay notional+fee, sells receive notional-fee."""
        return -(self.notional + self.fee) if self.side == "BUY" else self.notional - self.fee


def walk(levels, shares: Decimal, limit: Decimal | None, side: str, sc: Scenario, tick: Decimal,
         fee_rate: Decimal, top_price: Decimal | None = None) -> Fill:
    """Fill `shares` against sorted `levels` [(price, size)] (asks ascending for BUY, bids descending
    for SELL), respecting the limit price, the scenario depth haircut and adverse ticks."""
    f = Fill(side, shares, top_price=top_price if top_price is not None else (levels[0][0] if levels else None))
    adj = tick * sc.extra_ticks
    remaining = shares
    for price, size in levels:
        if remaining <= 0:
            break
        px = min(price + adj, D1 - tick) if side == "BUY" else max(price - adj, tick)
        if limit is not None and ((side == "BUY" and px > limit) or (side == "SELL" and px < limit)):
            break
        avail = (size * sc.depth_fraction).quantize(SHARE_Q, ROUND_DOWN)
        take = min(avail, remaining)
        if take <= 0:
            continue
        f.fills.append([str(px), str(take)])
        f.filled += take
        f.notional += px * take
        f.fee += taker_fee(take, px, fee_rate)
        remaining -= take
    return f


def buy_cost(levels, shares: Decimal, sc: Scenario, tick: Decimal, fee_rate: Decimal) -> Decimal | None:
    """Total USDC to buy exactly `shares` (incl. fees), or None if depth is insufficient."""
    f = walk(levels, shares, None, "BUY", sc, tick, fee_rate)
    return (f.notional + f.fee) if f.filled >= shares else None


def max_equal_shares(legs, budget: Decimal, sc: Scenario, payoff: Decimal, min_net_edge: Decimal,
                     min_size: Decimal) -> tuple[Decimal, Decimal]:
    """Largest q (2 dp) such that buying q shares of every leg costs <= budget and
    (q*payoff - cost)/cost >= min_net_edge. legs = [(ask_levels, tick, fee_rate)].
    Returns (q, total_cost); q == 0 when nothing qualifies. Net edge falls with q, so bisection is valid."""

    def ok(q: Decimal):
        total = D0
        for levels, tick, rate in legs:
            c = buy_cost(levels, q, sc, tick, rate)
            if c is None:
                return None
            total += c
        if total <= 0 or total > budget or (q * payoff - total) / total < min_net_edge:
            return None
        return total

    lo, hi = D0, budget / Decimal("0.001")  # cannot buy more shares than budget at the minimum price
    best = (D0, D0)
    for _ in range(60):
        mid = ((lo + hi) / 2).quantize(SHARE_Q, ROUND_DOWN)
        if mid <= lo:
            break
        cost = ok(mid)
        if cost is not None:
            lo, best = mid, (mid, cost)
        else:
            hi = mid
    if best[0] < min_size:
        return D0, D0
    return best


def shares_for_budget(levels, budget: Decimal, limit: Decimal, sc: Scenario, tick: Decimal,
                      fee_rate: Decimal) -> Decimal:
    """Max shares (2 dp) purchasable up to `limit` without spending more than `budget` incl. fees."""
    lo, hi = D0, budget / Decimal("0.001")
    for _ in range(60):
        mid = ((lo + hi) / 2).quantize(SHARE_Q, ROUND_DOWN)
        if mid <= lo:
            break
        f = walk(levels, mid, limit, "BUY", sc, tick, fee_rate)
        if f.filled >= mid and f.notional + f.fee <= budget:
            lo = mid
        else:
            hi = mid
    return lo
