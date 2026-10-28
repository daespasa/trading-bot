"""Backtest = replay of snapshots this app actually recorded. Nothing is reconstructed:
- each step's book has exactly one level per side: the recorded best bid/ask and the size at that level
  (deeper liquidity was not stored, so it is not assumed);
- orders fill against the *next* recorded step (latency = one snapshot interval), under the chosen scenario;
- positions settle only on official payouts known at the end of the window; the rest are marked at the bid.
Runs in a worker thread on a read-only connection; results go to `backtest_runs`, never to live wallets.
Crypto momentum needs Binance klines for the window and is fetched on demand; ML strategies are evaluated
by the walk-forward protocol instead and are not replayed here."""
import bisect
import json
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.analytics.metrics import evidence, max_drawdown
from app.core.config import settings
from app.market_data.binance import SYMBOLS, CryptoFeed
from app.market_data.book import Book
from app.market_data.collector import MarketView
from app.paper_trading.execution import D0, effective_fee_rate, max_equal_shares, scenario, shares_for_budget, walk
from app.strategies import REGISTRY
from app.strategies.base import Context

REPLAYABLE = ("arb_yes_no", "arb_multi", "related_markets", "mean_reversion", "crypto_momentum")


def _dt(s):
    d = datetime.fromisoformat(str(s).replace(" ", "T"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class HistCrypto(CryptoFeed):
    """CryptoFeed over historical 1m klines, cut at the replay clock (no look-ahead)."""

    def __init__(self, klines: dict[str, list[tuple[int, float]]]):
        super().__init__(http=None)
        self.all = klines
        self.closed_at = {s: [k[0] + 60000 for k in ks] for s, ks in klines.items()}
        self.index = {s: {k[0]: {"open": k[2], "close": k[1]} for k in ks} for s, ks in klines.items()}

    def at(self, now_ms: int):
        for s, ks in self.all.items():
            i = bisect.bisect_right(self.closed_at[s], now_ms)
            self.closes[s] = ks[max(0, i - 360):i]

    def fresh(self, max_age=120):
        return any(self.closes.values())


def _klines(start_ms: int, end_ms: int) -> dict:
    out = {}
    with httpx.Client(timeout=20) as http:
        for sym, pair in SYMBOLS.items():
            rows, t = [], start_ms - 6 * 3600 * 1000
            while t < end_ms:
                r = http.get(f"{settings.binance_url}/api/v3/klines",
                             params={"symbol": pair, "interval": "1m", "startTime": t, "limit": 1000})
                r.raise_for_status()
                ks = r.json()
                if not ks:
                    break
                rows += [(int(k[0]), float(k[4]), float(k[1])) for k in ks]  # (open_ms, close, open)
                t = int(ks[-1][0]) + 60000
            out[sym] = rows
    return out


def run(params: dict) -> dict:
    start, end = _dt(params["start"]), _dt(params["end"])
    strategies = [s for s in params.get("strategies", REPLAYABLE) if s in REPLAYABLE]
    sc = scenario(params.get("scenario", "REALISTIC"))
    capital = Decimal(str(params.get("capital", 100)))
    max_trade = Decimal(str(params.get("trade_usdc", 5)))
    max_open = int(params.get("max_open_positions", 5))
    overrides = params.get("configs", {})
    con = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        mrows = con.execute("SELECT * FROM markets WHERE id IN (SELECT DISTINCT market_id FROM snapshots "
                            "WHERE ts_received >= ? AND ts_received < ?)", (start.replace(tzinfo=None).isoformat(" "),
                                                                            end.replace(tzinfo=None).isoformat(" "))).fetchall()
        outs = defaultdict(list)
        for o in con.execute("SELECT * FROM outcomes"):
            outs[o["market_id"]].append(o)
        markets = {}
        for r in mrows:
            o = sorted(outs[r["id"]], key=lambda x: x["idx"])
            markets[r["id"]] = MarketView(
                r["id"], r["question"], r["event_id"], r["event_title"], r["group_title"], r["category"],
                bool(r["neg_risk"]), bool(r["event_exhaustive"]), _dt(r["end_date"]) if r["end_date"] else None,
                _dt(r["event_start"]) if r["event_start"] else None, bool(r["fees_enabled"]), Decimal(r["fee_rate"]),
                r["fee_base_bps"], Decimal(r["tick_size"]), Decimal(r["min_size"]), True, False, r["slug"],
                r["description"], [x["token_id"] for x in o], [x["name"] for x in o])
        payouts = {r["id"]: json.loads(r["payouts"]) for r in mrows
                   if r["resolved"] and r["payouts"] and r["resolved_at"] and _dt(r["resolved_at"]) <= end}
        crypto = None
        if "crypto_momentum" in strategies:
            crypto = HistCrypto(_klines(int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
        cur = con.execute("SELECT token_id, market_id, ts_received, best_bid, best_ask, bid_size, ask_size "
                          "FROM snapshots WHERE ts_received >= ? AND ts_received < ? ORDER BY ts_received",
                          (start.replace(tzinfo=None).isoformat(" "), end.replace(tzinfo=None).isoformat(" ")))
        books: dict[str, Book] = {}
        history: dict[str, deque] = defaultdict(lambda: deque(maxlen=720))
        wallets = {s: {"cash": capital, "pos": [], "closed": [], "fees": D0, "curve": [], "orders": 0, "signals": 0}
                   for s in strategies}
        pending: list = []  # (strategy, signal) to fill on the next step
        bucket, step_rows = None, []

        def step(ts: datetime):
            nonlocal pending
            for r in step_rows:
                b = books.setdefault(r["token_id"], Book(r["token_id"]))
                b.bids = {Decimal(str(r["best_bid"])): Decimal(str(r["bid_size"] or 0))} if r["best_bid"] is not None else {}
                b.asks = {Decimal(str(r["best_ask"])): Decimal(str(r["ask_size"] or 0))} if r["best_ask"] is not None else {}
                b.ts_received = ts.timestamp()
                if r["best_bid"] is not None and r["best_ask"] is not None:
                    history[r["token_id"]].append((ts.timestamp(), (r["best_bid"] + r["best_ask"]) / 2))
            for strat, sgn in pending:
                _fill(wallets[strat], sgn, books, markets, sc, max_trade, max_open, ts)
            pending = []
            for strat, w in wallets.items():
                _exits(w, books, markets, sc, ts)
            if crypto:
                crypto.at(int(ts.timestamp() * 1000))
            refs = {}
            if crypto:
                for m in markets.values():
                    ms = int(m.event_start.timestamp() * 1000) if m.event_start else None
                    if ms and ms + 60000 <= ts.timestamp() * 1000:
                        for sym in SYMBOLS:
                            if ms in crypto.index[sym]:
                                refs[(sym, ms)] = crypto.index[sym][ms]
            live = {k: m for k, m in markets.items() if m.end_date is None or m.end_date > ts}
            ev = defaultdict(list)
            for m in live.values():
                if m.event_id:
                    ev[m.event_id].append(m)
            ctx = Context(now=ts, markets=live, books=books, events=ev, history=history, crypto=crypto, refs=refs,
                          stale_seconds=settings.snapshot_seconds * 3, scenario=sc)
            for strat in strategies:
                cfg = dict(REGISTRY[strat].defaults) | overrides.get(strat, {})
                for sgn in REGISTRY[strat].evaluate(ctx, cfg):
                    if sgn.actionable:
                        wallets[strat]["signals"] += 1
                        pending.append((strat, sgn))
            for w in wallets.values():
                w["curve"].append(float(w["cash"] + sum((p["shares"] * (books[p["token"]].best_bid or D0)
                                                         if p["token"] in books else D0 for p in w["pos"]), D0)))

        for r in cur:
            ts = _dt(r["ts_received"])
            b = int(ts.timestamp()) // settings.snapshot_seconds
            if bucket is not None and b != bucket:
                step(datetime.fromtimestamp(bucket * settings.snapshot_seconds, timezone.utc))
                step_rows = []
            bucket = b
            step_rows.append(r)
        if step_rows:
            step(datetime.fromtimestamp(bucket * settings.snapshot_seconds, timezone.utc))
    finally:
        con.close()

    results = {}
    for strat, w in wallets.items():
        for p in list(w["pos"]):
            pay = payouts.get(p["market"])
            if pay is not None:
                idx = markets[p["market"]].tokens.index(p["token"])
                proceeds = p["shares"] * Decimal(str(pay[idx]))
                w["cash"] += proceeds
                w["closed"].append({**p, "pnl": float(proceeds - p["cost"]), "exit": "resolution"})
                w["pos"].remove(p)
        unreal = sum((p["shares"] * (books[p["token"]].best_bid or D0) - p["cost"] for p in w["pos"] if p["token"] in books), D0)
        groups = defaultdict(float)
        for c in w["closed"]:
            groups[c["group"]] += c["pnl"]
        equity = w["curve"] or [float(capital)]
        results[strat] = {
            "signals": w["signals"], "orders": w["orders"], "closed_trades": len(groups),
            "realized_pnl": sum(groups.values()), "unrealized_pnl": float(unreal), "open_positions": len(w["pos"]),
            "fees": float(w["fees"]), "final_cash": float(w["cash"]), "max_drawdown": max_drawdown(equity)[0],
            "evidence": evidence(list(groups.values())),
            "curve": equity[:: max(1, len(equity) // 300)],
        }
    return {"strategies": results, "markets": len(markets), "resolved_in_window": len(payouts),
            "assumptions": "top-of-book only; fills on next snapshot; official payouts only"}


def _fill(w, sgn, books, markets, sc, max_trade, max_open, ts):
    if len({p["group"] for p in w["pos"]}) >= max_open or {leg.market_id for leg in sgn.legs} & {p["market"] for p in w["pos"]}:
        return
    budget = min(max_trade, w["cash"])
    legs = [(leg, markets[leg.market_id], books.get(leg.token_id)) for leg in sgn.legs]
    if any(b is None or not b.asks for _, _, b in legs):
        return
    rates = [effective_fee_rate(m.fee_rate, m.fee_base_bps, sc) for _, m, _ in legs]
    min_size = max(m.min_size for _, m, _ in legs)
    if sgn.mode == "equal_shares":
        q, _ = max_equal_shares([(b.ask_levels(), m.tick, r) for (_, m, b), r in zip(legs, rates)], budget, sc,
                                sgn.payoff, sgn.min_net_edge, min_size)
        limits = [None] * len(legs)
    else:
        leg, m, b = legs[0]
        q = shares_for_budget(b.ask_levels(), budget, leg.limit, sc, m.tick, rates[0])
        limits = [leg.limit]
    if q < min_size:
        return
    fills = [walk(b.ask_levels(), q, lim, "BUY", sc, m.tick, r) for (_, m, b), r, lim in zip(legs, rates, limits)]
    cost = sum((f.notional + f.fee for f in fills), D0)
    if cost > w["cash"] or not any(f.filled > 0 for f in fills):
        return
    w["cash"] -= cost
    w["orders"] += len(fills)
    group = f"{sgn.key}@{ts.timestamp()}"
    for (leg, m, b), f in zip(legs, fills):
        if f.filled > 0:
            w["fees"] += f.fee
            w["pos"].append({"group": group, "market": leg.market_id, "token": leg.token_id, "shares": f.filled,
                             "cost": f.notional + f.fee, "entry": f.avg_price, "opened": ts, "exit_rules": sgn.exit_rules})


def _exits(w, books, markets, sc, ts):
    for p in list(w["pos"]):
        r, b = p["exit_rules"], books.get(p["token"])
        if not r or b is None or b.best_bid is None:
            continue
        bid = b.best_bid
        hit = (bid >= p["entry"] + Decimal(str(r["take_profit"])) or bid <= p["entry"] - Decimal(str(r["stop_loss"]))
               or (ts - p["opened"]).total_seconds() > r["max_hold_minutes"] * 60)
        if not hit:
            continue
        m = markets[p["market"]]
        f = walk(b.bid_levels(), p["shares"], None, "SELL", sc, m.tick, effective_fee_rate(m.fee_rate, m.fee_base_bps, sc))
        if f.filled <= 0:
            continue
        part = (p["cost"] * f.filled / p["shares"])
        w["cash"] += f.notional - f.fee
        w["fees"] += f.fee
        w["closed"].append({**p, "pnl": float(f.notional - f.fee - part), "exit": "rule"})
        if f.filled >= p["shares"]:
            w["pos"].remove(p)
        else:
            p["shares"] -= f.filled
            p["cost"] -= part
