"""BTC/ETH spot from Binance public klines (no key). Daily 'above'/'up or down' markets resolve on Binance
BTCUSDT/ETHUSDT 1m closes; 5m/15m markets resolve on Chainlink TWAP, for which Binance is a proxy."""
import logging
import math
import time

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


class CryptoFeed:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(timeout=15)
        self.closes: dict[str, list[tuple[int, float]]] = {}  # symbol -> [(open_ms, close)] last 6h of 1m
        self.updated: float = 0.0
        self._ref_cache: dict[tuple[str, int], dict] = {}

    async def refresh(self) -> None:
        for sym, pair in SYMBOLS.items():
            r = await self.http.get(f"{settings.binance_url}/api/v3/klines",
                                    params={"symbol": pair, "interval": "1m", "limit": 360})
            r.raise_for_status()
            self.closes[sym] = [(int(k[0]), float(k[4])) for k in r.json()]
        self.updated = time.time()

    def fresh(self, max_age: float = 120) -> bool:
        return bool(self.closes) and time.time() - self.updated < max_age

    def spot(self, sym: str) -> float | None:
        c = self.closes.get(sym)
        return c[-1][1] if c else None

    def ret(self, sym: str, minutes: int) -> float | None:
        c = self.closes.get(sym)
        if not c or len(c) <= minutes:
            return None
        return math.log(c[-1][1] / c[-1 - minutes][1])

    def vol_per_minute(self, sym: str, minutes: int = 240) -> float | None:
        c = self.closes.get(sym)
        if not c or len(c) < 30:
            return None
        xs = [k[1] for k in c[-minutes - 1:]]
        rets = [math.log(b / a) for a, b in zip(xs, xs[1:])]
        m = sum(rets) / len(rets)
        return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

    async def reference(self, sym: str, open_ms: int) -> dict | None:
        """1m candle opening at open_ms: {"open", "close"}; close is None until the candle has closed.
        'open' ~ price at the start of a window (TWAP markets), 'close' = Binance-resolved reference."""
        key = (sym, open_ms)
        if key in self._ref_cache and self._ref_cache[key]["close"] is not None:
            return self._ref_cache[key]
        r = await self.http.get(f"{settings.binance_url}/api/v3/klines",
                                params={"symbol": SYMBOLS[sym], "interval": "1m", "startTime": open_ms, "limit": 1})
        r.raise_for_status()
        k = r.json()
        if not k or int(k[0][0]) != open_ms:
            return None
        closed = int(k[0][6]) < time.time() * 1000
        ref = {"open": float(k[0][1]), "close": float(k[0][4]) if closed else None}
        if len(self._ref_cache) > 2000:
            for old in sorted(self._ref_cache)[:1000]:
                del self._ref_cache[old]
        self._ref_cache[key] = ref  # refetched by the engine while close is None
        return ref

    def window_average(self, sym: str, start_ms: int, now_ms: int) -> tuple[float, int] | None:
        """Mean of closed 1m closes inside [start, now] and how many minutes it covers (TWAP proxy)."""
        xs = [c for t, c, *_ in self.closes.get(sym, []) if t >= start_ms and t + 60000 <= now_ms]
        return (sum(xs) / len(xs), len(xs)) if xs else None
