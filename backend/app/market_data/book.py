"""In-memory L2 order book per token, maintained from WS `book` snapshots and `price_change` deltas."""
import time
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Book:
    token_id: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    ts_source: float | None = None  # epoch seconds from the exchange
    ts_received: float = 0.0  # epoch seconds, local monotonic-ish wall clock
    source: str = "ws"
    last_trade: Decimal | None = None

    def replace(self, bids, asks, ts_source: float | None, source: str) -> None:
        self.bids = {Decimal(l["price"]): Decimal(l["size"]) for l in bids if Decimal(l["size"]) > 0}
        self.asks = {Decimal(l["price"]): Decimal(l["size"]) for l in asks if Decimal(l["size"]) > 0}
        self.touch(ts_source, source)

    def apply(self, side: str, price: str, size: str, ts_source: float | None) -> None:
        book = self.bids if side.upper() == "BUY" else self.asks
        p, s = Decimal(price), Decimal(size)
        if s == 0:
            book.pop(p, None)
        else:
            book[p] = s
        self.touch(ts_source, "ws")

    def touch(self, ts_source: float | None, source: str) -> None:
        self.ts_source = ts_source
        self.ts_received = time.time()
        self.source = source

    def ask_levels(self) -> list[tuple[Decimal, Decimal]]:
        return sorted(self.asks.items())

    def bid_levels(self) -> list[tuple[Decimal, Decimal]]:
        return sorted(self.bids.items(), reverse=True)

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    def age(self, now: float | None = None) -> float:
        return (now or time.time()) - self.ts_received

    def depth_usdc(self, side: str, ticks: int = 5, tick: Decimal = Decimal("0.01")) -> Decimal:
        levels = self.ask_levels() if side == "ask" else self.bid_levels()
        if not levels:
            return Decimal(0)
        top = levels[0][0]
        lim = ticks * tick
        return sum((p * s for p, s in levels if abs(p - top) < lim), Decimal(0))

    def to_dict(self, n: int = 10) -> dict:
        return {
            "bids": [[str(p), str(s)] for p, s in self.bid_levels()[:n]],
            "asks": [[str(p), str(s)] for p, s in self.ask_levels()[:n]],
            "ts_source": self.ts_source,
            "ts_received": self.ts_received,
            "source": self.source,
        }

    def copy(self) -> "Book":
        return Book(self.token_id, dict(self.bids), dict(self.asks), self.ts_source, self.ts_received, self.source, self.last_trade)
