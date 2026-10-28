"""Market data collector: discovery (Gamma), live books (CLOB WebSocket + periodic REST resync),
snapshot persistence and resolution polling. All state the strategies read lives in `MarketState`."""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import websockets
from sqlalchemy import select, update

from app.core.config import settings
from app.database import session as db
from app.database.models import Market, Outcome, Position, Prediction, Snapshot
from app.market_data.binance import CryptoFeed
from app.market_data.book import Book
from app.market_data.polymarket import PolymarketClient, event_exhaustive, parse_market

log = logging.getLogger(__name__)
SHORT_CRYPTO_PREFIXES = ("btc-updown", "eth-updown")


@dataclass
class MarketView:
    id: str
    question: str
    event_id: str | None
    event_title: str | None
    group_title: str | None
    category: str
    neg_risk: bool
    event_exhaustive: bool
    end_date: datetime | None
    event_start: datetime | None
    fees_enabled: bool
    fee_rate: Decimal
    fee_base_bps: int
    tick: Decimal
    min_size: Decimal
    accepting_orders: bool
    closed: bool
    slug: str
    description: str
    tokens: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, m: Market, outs: list[Outcome]) -> "MarketView":
        outs = sorted(outs, key=lambda o: o.idx)
        return cls(m.id, m.question, m.event_id, m.event_title, m.group_title, m.category, m.neg_risk,
                   m.event_exhaustive, m.end_date, m.event_start, m.fees_enabled, m.fee_rate, m.fee_base_bps,
                   m.tick_size, m.min_size, m.accepting_orders, m.closed, m.slug, m.description,
                   [o.token_id for o in outs], [o.name for o in outs])


@dataclass
class FeedStatus:
    connected: bool = False
    last_message: float = 0.0
    down_since: float | None = field(default_factory=time.time)
    reconnects: int = 0
    messages: int = 0
    last_discovery: float = 0.0
    last_resync: float = 0.0
    errors: int = 0


class MarketState:
    def __init__(self):
        self.markets: dict[str, MarketView] = {}
        self.books: dict[str, Book] = {}
        self.token_market: dict[str, str] = {}
        self.history: dict[str, deque] = {}  # token -> deque[(epoch, mid)] for ~2h
        self.feed = FeedStatus()
        self.crypto = CryptoFeed()

    def healthy(self) -> bool:
        f = self.feed
        return f.connected and time.time() - f.last_message < settings.ws_down_halt_seconds

    def events(self) -> dict[str, list[MarketView]]:
        out: dict[str, list[MarketView]] = {}
        for m in self.markets.values():
            if m.event_id:
                out.setdefault(m.event_id, []).append(m)
        return out

    def price_of(self, token_id: str) -> Decimal | None:
        b = self.books.get(token_id)
        return b.best_bid if b else None


class Collector:
    def __init__(self, state: MarketState, client: PolymarketClient | None = None):
        self.state = state
        self.client = client or PolymarketClient()
        self.ws = None
        self.subscribed: set[str] = set()
        self.max_markets = settings.markets
        self.crypto_slots = 4
        self.min_liquidity = 1000.0
        self.resolution_wakeup = asyncio.Event()

    # ---------- discovery ----------
    async def discover(self) -> None:
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        selected: dict[str, tuple[dict, dict]] = {}

        short = await self.client.events(active="true", closed="false", tag_slug="up-or-down", order="endDate",
                                         ascending="true", end_date_min=now_iso, limit=60)
        cands = []
        for ev in short:
            if not str(ev.get("slug", "")).startswith(SHORT_CRYPTO_PREFIXES):
                continue
            for m in ev.get("markets") or []:
                start = m.get("eventStartTime")
                if start and datetime.fromisoformat(start.replace("Z", "+00:00")) <= now + timedelta(minutes=10) \
                        and m.get("acceptingOrders"):
                    cands.append((m.get("endDate"), m, ev))
        for _, m, ev in sorted(cands, key=lambda c: c[0])[: self.crypto_slots]:
            selected[str(m["id"])] = (m, ev)

        events = await self.client.events(active="true", closed="false", order="volume24hr", ascending="false",
                                          end_date_min=now_iso, limit=80)
        for ev in events:
            room = self.max_markets - len(selected)
            if room <= 0:
                break
            if str(ev.get("slug", "")).startswith(SHORT_CRYPTO_PREFIXES):
                continue
            open_ms = [m for m in ev.get("markets") or [] if not m.get("closed") and m.get("active")
                       and m.get("enableOrderBook") and m.get("acceptingOrders")
                       and float(m.get("liquidityNum") or 0) >= self.min_liquidity]
            if not open_ms:
                continue
            if len(open_ms) <= min(room, 12):
                chosen = open_ms
            else:
                chosen = sorted(open_ms, key=lambda m: -float(m.get("volume24hr") or 0))[: min(room, 2)]
            for m in chosen:
                selected[str(m["id"])] = (m, ev)

        ids = set(selected)
        rows = []
        for m, ev in selected.values():
            parsed = parse_market(m, ev)
            if parsed:
                parsed[0]["event_exhaustive"] = event_exhaustive(ev, ids)
                rows.append(parsed)
        await self._upsert(rows, monitored_ids={r[0]["id"] for r in rows})
        await self._fetch_fee_bases()
        await self.load_state()
        self.state.feed.last_discovery = time.time()
        await self.sync_subscriptions()

    async def _upsert(self, rows, monitored_ids: set[str]) -> None:
        async with db.write() as s:
            held = set((await s.scalars(select(Position.market_id).where(Position.status == "open"))).all())
            await s.execute(update(Market).values(monitored=False))
            for row, outs in rows:
                existing = await s.get(Market, row["id"])
                if existing:
                    for k, v in row.items():
                        setattr(existing, k, v)
                else:
                    s.add(Market(**row))
                for o in outs:
                    if not await s.get(Outcome, o["token_id"]):
                        s.add(Outcome(**o))
            await s.flush()
            keep = monitored_ids | held
            await s.execute(update(Market).where(Market.id.in_(keep), Market.resolved.is_(False)).values(monitored=True))

    async def _fetch_fee_bases(self) -> None:
        async with db.read() as s:
            todo = (await s.execute(select(Market.id, Outcome.token_id).join(Outcome, Outcome.market_id == Market.id)
                                    .where(Market.monitored, Market.fees_enabled, Market.fee_base_bps == 0,
                                           Outcome.idx == 0))).all()
        for mid, tok in todo[:30]:
            bps = await self.client.fee_base_bps(tok)
            async with db.write() as s:
                await s.execute(update(Market).where(Market.id == mid).values(fee_base_bps=bps))

    async def load_state(self) -> None:
        """(Re)build the in-memory market views from the DB: also the restart-recovery path."""
        async with db.read() as s:
            ms = (await s.scalars(select(Market).where(Market.monitored))).all()
            outs = (await s.scalars(select(Outcome).where(Outcome.market_id.in_([m.id for m in ms])))).all()
        by_m: dict[str, list[Outcome]] = {}
        for o in outs:
            by_m.setdefault(o.market_id, []).append(o)
        views = {m.id: MarketView.from_row(m, by_m.get(m.id, [])) for m in ms}
        self.state.markets = views
        self.state.token_market = {t: v.id for v in views.values() for t in v.tokens}
        for t in self.state.token_market:
            self.state.books.setdefault(t, Book(t, ts_received=0.0))
            self.state.history.setdefault(t, deque(maxlen=max(10, 7200 // settings.snapshot_seconds)))
        for t in list(self.state.books):
            if t not in self.state.token_market:
                del self.state.books[t]
                self.state.history.pop(t, None)

    async def warm_history(self) -> None:
        since = datetime.now(timezone.utc) - timedelta(hours=2)
        async with db.read() as s:
            rows = (await s.execute(select(Snapshot.token_id, Snapshot.ts_received, Snapshot.best_bid, Snapshot.best_ask)
                                    .where(Snapshot.ts_received >= since).order_by(Snapshot.ts_received))).all()
        for tok, ts, bid, ask in rows:
            if tok in self.state.history and bid is not None and ask is not None:
                self.state.history[tok].append((ts.timestamp(), (bid + ask) / 2))

    # ---------- websocket ----------
    async def sync_subscriptions(self) -> None:
        want = set(self.state.token_market)
        if self.ws is None:
            return
        add, rem = want - self.subscribed, self.subscribed - want
        try:
            if add:
                await self.ws.send(json.dumps({"assets_ids": sorted(add), "operation": "subscribe",
                                               "custom_feature_enabled": True}))
            if rem:
                await self.ws.send(json.dumps({"assets_ids": sorted(rem), "operation": "unsubscribe"}))
            self.subscribed = want
        except Exception as e:  # connection loop will reconnect and resubscribe everything
            log.warning("subscription update failed: %s", e)

    async def run_ws(self) -> None:
        backoff = 1
        f = self.state.feed
        while True:
            try:
                async with websockets.connect(settings.ws_url, ping_interval=None, max_size=2 ** 22) as ws:
                    self.ws = ws
                    self.subscribed = set(self.state.token_market)
                    await ws.send(json.dumps({"assets_ids": sorted(self.subscribed), "type": "market",
                                              "custom_feature_enabled": True}))
                    f.connected, f.down_since, f.last_message = True, None, time.time()
                    backoff = 1
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for raw in ws:
                            f.last_message = time.time()
                            f.messages += 1
                            if raw == "PONG":
                                continue
                            self._handle(json.loads(raw))
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("ws disconnected: %s", e)
                f.errors += 1
            self.ws = None
            f.connected = False
            f.down_since = f.down_since or time.time()
            f.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(10)
            await ws.send("PING")

    def _handle(self, msg) -> None:
        for ev in msg if isinstance(msg, list) else [msg]:
            et = ev.get("event_type")
            ts = float(ev["timestamp"]) / 1000 if ev.get("timestamp") else None
            if et == "book":
                b = self.state.books.get(ev.get("asset_id"))
                if b:
                    b.replace(ev.get("bids") or [], ev.get("asks") or [], ts, "ws")
            elif et == "price_change":
                for pc in ev.get("price_changes") or []:
                    b = self.state.books.get(pc.get("asset_id"))
                    if b and b.ts_received:  # only apply deltas on top of a snapshot
                        b.apply(pc["side"], pc["price"], pc["size"], ts)
            elif et == "last_trade_price":
                b = self.state.books.get(ev.get("asset_id"))
                if b:
                    b.last_trade = Decimal(str(ev["price"]))
            elif et == "tick_size_change":
                mid = self.state.token_market.get(ev.get("asset_id"))
                if mid in self.state.markets:
                    self.state.markets[mid].tick = Decimal(str(ev["new_tick_size"]))
            elif et == "market_resolved":
                self.resolution_wakeup.set()

    # ---------- REST resync + snapshots ----------
    async def resync_books(self) -> None:
        toks = list(self.state.token_market)
        if not toks:
            return
        for bk in await self.client.books(toks):
            b = self.state.books.get(bk.get("asset_id"))
            ts = float(bk["timestamp"]) / 1000 if bk.get("timestamp") else None
            if b is not None and (b.ts_source is None or ts is None or ts >= b.ts_source):
                b.replace(bk.get("bids") or [], bk.get("asks") or [], ts, "rest")
        self.state.feed.last_resync = time.time()

    async def write_snapshots(self) -> int:
        now = datetime.now(timezone.utc)
        rows = []
        for tok, b in self.state.books.items():
            if not b.ts_received:
                continue
            bb, ba = b.best_bid, b.best_ask
            mv = self.state.markets.get(self.state.token_market.get(tok, ""))
            tick = mv.tick if mv else Decimal("0.01")
            rows.append(Snapshot(
                token_id=tok, market_id=self.state.token_market.get(tok, ""),
                ts_source=datetime.fromtimestamp(b.ts_source, timezone.utc) if b.ts_source else None,
                ts_received=datetime.fromtimestamp(b.ts_received, timezone.utc),
                best_bid=float(bb) if bb is not None else None, best_ask=float(ba) if ba is not None else None,
                bid_size=float(b.bids[bb]) if bb is not None else None, ask_size=float(b.asks[ba]) if ba is not None else None,
                bid_depth=float(b.depth_usdc("bid", 5, tick)), ask_depth=float(b.depth_usdc("ask", 5, tick)),
                last_trade=float(b.last_trade) if b.last_trade is not None else None, source=b.source))
            if bb is not None and ba is not None:
                self.state.history[tok].append((now.timestamp(), float(bb + ba) / 2))
        if rows:
            async with db.write() as s:
                s.add_all(rows)
        return len(rows)

    # ---------- resolution ----------
    async def check_resolutions(self) -> list[str]:
        """Fetch markets that may have resolved; persist payouts. Returns newly resolved market ids."""
        now = datetime.now(timezone.utc)
        async with db.read() as s:
            ids = set((await s.scalars(select(Position.market_id).where(Position.status == "open"))).all())
            ids |= set((await s.scalars(select(Market.id).where(Market.monitored, Market.end_date < now))).all())
            ids |= set((await s.scalars(select(Prediction.market_id).where(Prediction.outcome.is_(None))
                                        .distinct().limit(200))).all())
            unresolved = set((await s.scalars(select(Market.id).where(Market.id.in_(ids), Market.resolved.is_(False)))).all())
        if not unresolved:
            return []
        newly = []
        # Gamma omits closed markets from id lookups unless closed=true is passed
        for m in await self.client.markets_by_ids(sorted(unresolved), closed="true"):
            parsed = parse_market(m)
            if not parsed or not parsed[0]["resolved"]:
                continue
            row = parsed[0]
            async with db.write() as s:
                mk = await s.get(Market, row["id"])
                mk.closed, mk.resolved, mk.payouts = True, True, row["payouts"]
                mk.resolved_at, mk.monitored, mk.active = row["resolved_at"] or now, False, False
            newly.append(row["id"])
        return newly
