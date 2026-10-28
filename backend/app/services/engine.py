"""Orchestrator: one asyncio process running collector, strategy scan, risk, paper execution, settlement,
equity, predictions, training scheduling, retention, reports and resource monitoring."""
import asyncio
import json
import logging
import os
import signal as sig
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import psutil
from sqlalchemy import delete, select, text, update

from app.analytics import reports
from app.core.config import settings
from app.database import session as db
from app.analytics.validation import compare_fills, run_print_checks, top, worse_fill
from app.database.models import (Decision, EquitySnapshot, ExecutionCheck, Market, Opportunity, Outcome, Position,
                                 Prediction, Snapshot,
                                 StrategyConfig, SystemEvent, TrainingRun, Wallet)
from app.machine_learning.predictor import Predictor
from app.market_data.book import Book
from app.market_data.collector import Collector, MarketState
from app.paper_trading import portfolio as pf
from app.paper_trading.execution import (D0, SHARE_Q, configure_scenarios, scenario, effective_fee_rate, max_equal_shares,
                                         shares_for_budget, taker_fee, walk)
from app.paper_trading.risk import check as risk_check
from app.strategies import REGISTRY
from app.strategies.base import Context, Signal

log = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[2]
RUNTIME_FILE = settings.data_dir / "runtime_settings.json"
RUNTIME_DEFAULTS = {"max_markets": settings.markets, "retrain_hours": 24, "retention_days": settings.retention_days,
                    "prediction_minutes": 15, "notifications": True, "auto_train": True, "scenarios": {},
                    "kill_switch": False}


class Engine:
    def __init__(self):
        self.state = MarketState()
        self.collector = Collector(self.state)
        self.predictor = Predictor(self.collector.client)
        self.tasks: list[asyncio.Task] = []
        self.started = time.time()
        self.wallet_locks: dict[int, asyncio.Lock] = {}
        self.inflight: set[tuple[int, int]] = set()
        self.opp_cache: dict[str, tuple[int, float, float]] = {}  # key -> (id, last_seen, last_write)
        self.configs: dict[str, StrategyConfig] = {}
        self.decisions: deque = deque(maxlen=300)
        self.rejections: dict[str, int] = {}
        self.dirty: set[str] = set()
        self.notifications: deque = deque(maxlen=50)
        self.resources: deque = deque(maxlen=240)
        self.train_proc: asyncio.subprocess.Process | None = None
        self.train_paused = False
        self.loop_errors: dict[str, str] = {}
        self.runtime = self._load_runtime()
        try:
            configure_scenarios(self.runtime.get("scenarios", {}))
        except ValueError:
            log.error("invalid scenario overrides in runtime settings; using defaults")
        self.collector.max_markets = int(self.runtime["max_markets"])
        self.last_scan_ms = 0.0

    # ---------- runtime settings ----------
    def _load_runtime(self) -> dict:
        try:
            return RUNTIME_DEFAULTS | json.loads(RUNTIME_FILE.read_text())
        except (OSError, ValueError):
            return dict(RUNTIME_DEFAULTS)

    def update_runtime(self, changes: dict) -> dict:
        allowed = {k: v for k, v in changes.items() if k in RUNTIME_DEFAULTS}
        if "scenarios" in allowed:
            configure_scenarios(allowed["scenarios"])  # raises ValueError on invalid parameters
        if "max_markets" in allowed:
            allowed["max_markets"] = max(1, min(int(allowed["max_markets"]), settings.max_markets))
        self.runtime |= allowed
        RUNTIME_FILE.write_text(json.dumps(self.runtime, indent=2))
        self.collector.max_markets = int(self.runtime["max_markets"])
        return self.runtime

    # ---------- lifecycle ----------
    async def start(self) -> None:
        await self._bootstrap()
        await self.collector.load_state()
        await self.collector.warm_history()
        if not settings.collector_enabled:
            return
        self.tasks = [asyncio.create_task(self.collector.run_ws(), name="ws")]
        for name, every, fn in [
            ("discovery", 60, self.collector.discover), ("resync", 60, self.collector.resync_books),
            ("crypto", 20, self.state.crypto.refresh), ("snapshots", settings.snapshot_seconds, self.collector.write_snapshots),
            ("scan", settings.scan_seconds, self.scan), ("resolution", 120, self.resolve),
            ("equity", 300, self.snapshot_equity), ("predict", 60 * self.runtime["prediction_minutes"], self.predict),
            ("training", 600, self.maybe_train), ("retention", 6 * 3600, self.retention),
            ("reports", 600, self.make_reports), ("resources", 30, self.sample_resources),
            ("prints", 600, self.print_checks), ("gates", 600, self.update_gates),
        ]:
            self.tasks.append(asyncio.create_task(self._loop(name, every, fn), name=name))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.train_proc and self.train_proc.returncode is None:
            self.train_proc.terminate()

    async def _loop(self, name: str, every: float, fn) -> None:
        await asyncio.sleep(1 if name != "training" else 120)
        while True:
            t0 = time.time()
            try:
                if name == "resolution":
                    self.collector.resolution_wakeup.clear()
                await fn()
                self.loop_errors.pop(name, None)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("loop %s failed", name)
                self.loop_errors[name] = repr(e)[:300]
                await self.event("error", f"loop:{name}", repr(e)[:500])
            wait = max(0.5, every - (time.time() - t0))
            if name == "resolution":
                try:
                    await asyncio.wait_for(self.collector.resolution_wakeup.wait(), wait)
                    await asyncio.sleep(15)  # Gamma lags the WS resolution event slightly
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(wait)

    async def _bootstrap(self) -> None:
        async with db.write() as s:
            for name, strat in REGISTRY.items():
                row = await s.get(StrategyConfig, name)
                if row is None:
                    s.add(StrategyConfig(name=name, enabled=True, config=dict(strat.defaults)))
                else:  # add new default keys without overwriting user values
                    row.config = dict(strat.defaults) | dict(row.config)
            if not (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).first():
                for name, strats in pf.DEFAULT_WALLETS:
                    await pf.create_wallet(s, name, strats, settings.initial_capital, max_trade_usdc=settings.max_trade_usdc,
                                           max_open_positions=settings.max_open_positions,
                                           max_daily_loss=settings.max_daily_loss)
            await s.execute(update(TrainingRun).where(TrainingRun.status == "running")
                            .values(status="failed", message="interrupted by restart"))
            since = datetime.now(timezone.utc) - timedelta(minutes=10)
            for o in (await s.scalars(select(Opportunity).where(Opportunity.last_seen >= since))).all():
                self.opp_cache[o.key] = (o.id, o.last_seen.timestamp(), o.last_seen.timestamp())
        await self.reload_configs()
        await self.event("info", "startup", "engine started", {"profile": settings.resource_profile})

    async def reload_configs(self) -> None:
        async with db.read() as s:
            self.configs = {c.name: c for c in (await s.scalars(select(StrategyConfig))).all()}

    async def event(self, level: str, kind: str, message: str, data: dict | None = None) -> None:
        try:
            async with db.write() as s:
                s.add(SystemEvent(level=level, kind=kind, message=message, data=data or {}))
        except Exception:
            log.exception("could not store event")
        if level in ("error", "warning") or kind in ("trade", "settlement", "training"):
            self.notifications.appendleft({"ts": datetime.now(timezone.utc).isoformat(), "level": level,
                                           "kind": kind, "message": message})
        self.dirty.add("events")

    # ---------- scan ----------
    def context(self) -> Context:
        return Context(now=datetime.now(timezone.utc), markets=self.state.markets, books=self.state.books,
                       events=self.state.events(), history=self.state.history, crypto=self.state.crypto,
                       refs=self.state.crypto._ref_cache, predictions=self.predictor.latest,
                       stale_seconds=settings.stale_seconds)

    async def _ensure_refs(self) -> None:
        """Fetch 'price to beat' reference candles for live Up/Down markets (strategies stay synchronous)."""
        from app.strategies.rules import crypto_target
        now = datetime.now(timezone.utc)
        for m in self.state.markets.values():
            t = crypto_target(m)
            if t and t[1] == "updown" and m.event_start and m.event_start <= now:
                key = (t[0], int(m.event_start.timestamp() * 1000))
                cached = self.state.crypto._ref_cache.get(key)
                if cached is None or cached["close"] is None:
                    try:
                        await self.state.crypto.reference(*key)
                    except Exception as e:
                        log.debug("ref fetch failed %s", e)

    async def scan(self) -> None:
        t0 = time.time()
        await self._ensure_refs()
        ctx = self.context()
        signals: list[Signal] = []
        for name, strat in REGISTRY.items():
            c = self.configs.get(name)
            if c is None or not c.enabled:
                continue
            try:
                signals += strat.evaluate(ctx, dict(strat.defaults) | dict(c.config))
            except Exception as e:
                log.exception("strategy %s failed", name)
                self.loop_errors[f"strategy:{name}"] = repr(e)[:300]
        opp_ids = await self._persist_opportunities(signals)
        if signals:
            self.dirty.add("opportunities")
        async with db.read() as s:
            wallets = (await s.scalars(select(Wallet).where(Wallet.status == "running"))).all()
        # kill switch: keep detecting and logging opportunities but open nothing; exits (risk reduction) still run
        openers = [] if self.runtime.get("kill_switch") else wallets
        for sgn in signals:
            if not sgn.actionable:
                continue
            oid = opp_ids[sgn.key]
            for w in openers:
                if sgn.strategy in w.strategies and (w.id, oid) not in self.inflight:
                    self.inflight.add((w.id, oid))
                    asyncio.create_task(self._execute_guarded(w.id, sgn, oid))
        await self._check_exits(wallets)
        self.last_scan_ms = (time.time() - t0) * 1000

    async def _persist_opportunities(self, signals: list[Signal]) -> dict[str, int]:
        now = time.time()
        ids: dict[str, int] = {}
        async with db.write() as s:
            for sgn in signals:
                cached = self.opp_cache.get(sgn.key)
                status = "detected" if sgn.actionable else "rejected"
                if cached and now - cached[1] < 600:
                    oid, _, last_write = cached
                    ids[sgn.key] = oid
                    if now - last_write >= 30:  # throttle updates of persistent opportunities
                        o = await s.get(Opportunity, oid)
                        if o:
                            o.last_seen = datetime.now(timezone.utc)
                            o.seen_count += 1
                            o.gross_edge, o.costs, o.net_edge, o.liquidity = sgn.gross_edge, sgn.costs, sgn.net_edge, sgn.liquidity
                            if o.status != "executed":
                                o.status, o.reason = status, sgn.reason
                            o.details = sgn.to_json()["details"]
                        self.opp_cache[sgn.key] = (oid, now, now)
                    else:
                        self.opp_cache[sgn.key] = (oid, now, last_write)
                    continue
                o = Opportunity(key=sgn.key, strategy=sgn.strategy, market_id=sgn.market_id, event_id=sgn.event_id,
                                title=sgn.title[:500], kind=sgn.kind, guaranteed=sgn.guaranteed, gross_edge=sgn.gross_edge,
                                costs=sgn.costs, net_edge=sgn.net_edge, liquidity=sgn.liquidity, risk=sgn.risk,
                                status=status, reason=sgn.reason, details=sgn.to_json())
                s.add(o)
                await s.flush()
                ids[sgn.key] = o.id
                self.opp_cache[sgn.key] = (o.id, now, now)
        if len(self.opp_cache) > 5000:
            self.opp_cache = {k: v for k, v in self.opp_cache.items() if now - v[1] < 600}
        return ids

    # ---------- execution ----------
    async def _execute_guarded(self, wallet_id: int, sgn: Signal, opp_id: int) -> None:
        try:
            await self.execute(wallet_id, sgn, opp_id)
        except Exception as e:
            log.exception("execution failed")
            await self.event("error", "execution", repr(e)[:500], {"wallet": wallet_id, "opportunity": opp_id})
        finally:
            await asyncio.sleep(30)  # keep the (wallet, opportunity) pair blocked a bit to avoid hot loops
            self.inflight.discard((wallet_id, opp_id))

    async def _decide(self, wallet: Wallet, sgn: Signal, opp_id: int, outcome: str, detail: str = "") -> None:
        """Record a wallet decision in memory (live view) and in `decisions` (survives restarts).
        Must not be called while holding the DB write lock."""
        now = datetime.now(timezone.utc)
        self.decisions.appendleft({"ts": now.isoformat(), "wallet": wallet.name, "strategy": sgn.strategy,
                                   "opportunity_id": opp_id, "title": sgn.title[:120], "decision": outcome,
                                   "detail": detail})
        if outcome == "rejected":
            k = f"{wallet.name}|{detail.split(':')[0]}"
            self.rejections[k] = self.rejections.get(k, 0) + 1
        reason = (detail.split(":")[0] if outcome == "executed" else detail)[:300]
        try:
            async with db.write() as s:
                row = await s.scalar(select(Decision).where(Decision.wallet_id == wallet.id,
                                                            Decision.opportunity_id == opp_id,
                                                            Decision.decision == outcome, Decision.reason == reason))
                if row:
                    row.count += 1
                    row.last_ts = now
                else:
                    s.add(Decision(wallet_id=wallet.id, opportunity_id=opp_id, strategy=sgn.strategy,
                                   decision=outcome, reason=reason))
        except Exception:
            log.exception("could not persist decision")

    async def _equity_and_day_start(self, s, w: Wallet) -> tuple[Decimal, Decimal]:
        pos = await pf.open_positions(s, w.id)
        eq = w.cash + pf.mark_value(pos, self.state.price_of)
        day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = await s.scalar(select(EquitySnapshot.equity).where(EquitySnapshot.wallet_id == w.id,
                                                                  EquitySnapshot.ts >= day0).order_by(EquitySnapshot.ts).limit(1))
        return eq, (start if start is not None else eq)

    async def execute(self, wallet_id: int, sgn: Signal, opp_id: int) -> None:
        lock = self.wallet_locks.setdefault(wallet_id, asyncio.Lock())
        async with lock:
            async with db.read() as s:
                w = await s.get(Wallet, wallet_id)
                if await pf.order_exists(s, f"w{wallet_id}:o{opp_id}:L0"):
                    return
                open_pos = await pf.open_positions(s, wallet_id)
                eq, day0 = await self._equity_and_day_start(s, w)
            reasons = risk_check(w, open_pos, eq, day0, sgn, self.state.healthy(), self.state.markets,
                                 datetime.now(timezone.utc))
            if reasons:
                await self._decide(w, sgn, opp_id, "rejected", "; ".join(reasons))
                return
            sc = scenario(w.scenario)
            cfg = dict(REGISTRY[sgn.strategy].defaults) | dict(self.configs[sgn.strategy].config)
            budget = min(w.max_trade_usdc, Decimal(str(cfg.get("trade_usdc", 5))), w.cash)
            plan = self._plan(sgn, budget, sc)
            if isinstance(plan, str):
                await self._decide(w, sgn, opp_id, "rejected", plan)
                return
            q, legs, decision_books = plan
            await asyncio.sleep(sc.latency_ms / 1000)  # signal -> order arrival
            unc = [f"assumes {sc.depth_fraction:.0%} of displayed depth was ours; queue position unknown"]
            # Execution Validator: also fetch the exchange's own book now and fill against the worse of the two.
            rest: dict[str, Book] = {}
            try:
                raw = await asyncio.wait_for(self.collector.client.books([leg.token_id for leg, _, _ in legs]), 3)
                for bk in raw:
                    rb = Book(bk["asset_id"])
                    rb.replace(bk.get("bids") or [], bk.get("asks") or [], None, "rest")
                    rest[bk["asset_id"]] = rb
            except Exception as e:
                unc.append(f"exchange book not verified ({type(e).__name__})")
            fills, checks = [], []
            for leg, mv, limit in legs:
                b = self.state.books[leg.token_id].copy()
                rate = effective_fee_rate(mv.fee_rate, mv.fee_base_bps, sc)
                f = walk(b.ask_levels(), q, limit, "BUY", sc, mv.tick, rate)
                check = None
                rb = rest.get(leg.token_id)
                if rb is not None:
                    fr = walk(rb.ask_levels(), q, limit, "BUY", sc, mv.tick, rate)
                    check = {"result": compare_fills(f, fr),
                             "ws": {"top_ask": top(b.ask_levels()), "filled": str(f.filled), "avg": str(f.avg_price)},
                             "rest": {"top_ask": top(rb.ask_levels()), "filled": str(fr.filled), "avg": str(fr.avg_price)}}
                    f = worse_fill(f, fr)
                fills.append((leg, f, b, b.age()))
                checks.append(check)
            if any(age > 10 for *_, age in fills):
                unc.append("book older than 10s at fill time")
            if sgn.mode == "equal_shares" and len({f.filled for _, f, _, _ in fills}) > 1:
                unc.append("legs filled unequally: residual exposure is unhedged")
            total = sum((f.notional + f.fee for _, f, _, _ in fills), D0)
            no_cash = False
            async with db.write() as s:
                w = await s.get(Wallet, wallet_id)
                if total > w.cash:
                    no_cash = True
                else:
                    for i, ((leg, f, b, _), check) in enumerate(zip(fills, checks)):
                        try:
                            order = await pf.record_buy(
                                s, w, f, client_order_id=f"w{wallet_id}:o{opp_id}:L{i}",
                                group_id=f"w{wallet_id}:o{opp_id}", strategy=sgn.strategy, market_id=leg.market_id,
                                token_id=leg.token_id, outcome=leg.outcome, scenario=sc.name,
                                latency_ms=sc.latency_ms, book=b.to_dict(),
                                signal=sgn.to_json() | {"opportunity_id": opp_id, "decision_books": decision_books},
                                uncertainty="; ".join(unc), exit_rules=sgn.exit_rules)
                        except pf.DuplicateOrder:
                            return
                        if check:
                            s.add(ExecutionCheck(order_id=order.id, kind="rest_book", result=check.pop("result"),
                                                 details=check))
                    filled = [f for _, f, _, _ in fills if f.filled > 0]
                    if filled:
                        await s.execute(update(Opportunity).where(Opportunity.id == opp_id).values(status="executed"))
            if no_cash:
                await self._decide(w, sgn, opp_id, "rejected", f"insufficient cash after latency: {total}")
                return
            self.dirty |= {"orders", "positions", "wallets"}
            if filled:
                await self._decide(w, sgn, opp_id, "executed", f"{q} shares x {len(fills)} legs: cost {total:.4f}")
                await self.event("info", "trade", f"{w.name}: {sgn.strategy} {sgn.title[:80]} cost {total:.2f} USDC",
                                 {"wallet": w.id, "opportunity": opp_id})
            else:
                await self._decide(w, sgn, opp_id, "no_fill", "no fill within limit after latency")

    def _plan(self, sgn: Signal, budget: Decimal, sc):
        """-> (shares, [(leg, market_view, limit)]) or a rejection reason. Sized so the worst case at the
        limit prices (incl. fees) cannot exceed the budget."""
        legs = []
        for leg in sgn.legs:
            mv = self.state.markets.get(leg.market_id)
            b = self.state.books.get(leg.token_id)
            if mv is None or b is None or not b.asks:
                return "missing book"
            legs.append((leg, mv, b))
        min_size = max(mv.min_size for _, mv, _ in legs)
        if sgn.mode == "equal_shares":
            spec = [(b.ask_levels(), mv.tick, effective_fee_rate(mv.fee_rate, mv.fee_base_bps, sc)) for _, mv, b in legs]
            q, _ = max_equal_shares(spec, budget, sc, sgn.payoff, sgn.min_net_edge + sc.edge_margin, min_size)
            if q == 0:
                return f"no size meets min net edge {sgn.min_net_edge} under {sc.name}"
            limits = []
            for (leg, mv, b), (lv, tick, rate) in zip(legs, spec):
                f = walk(lv, q, None, "BUY", sc, tick, rate)
                limits.append(Decimal(f.fills[-1][0]))
        else:
            leg, mv, b = legs[0]
            rate = effective_fee_rate(mv.fee_rate, mv.fee_base_bps, sc)
            limit = (leg.limit - sc.edge_margin).quantize(mv.tick, ROUND_DOWN)  # scenario safety margin
            q = shares_for_budget(b.ask_levels(), budget, limit, sc, mv.tick, rate)
            limits = [limit]
        worst = sum((lim + taker_fee(Decimal(1), lim, effective_fee_rate(mv.fee_rate, mv.fee_base_bps, sc))
                     for lim, (_, mv, _) in zip(limits, legs)), D0)
        if worst > 0:
            q = min(q, (budget / worst).quantize(SHARE_Q, ROUND_DOWN))
        if q < min_size:
            return f"size {q} below minimum order size {min_size}"
        books = {leg.token_id: b.to_dict(10) for leg, _, b in legs}
        return q, [(leg, mv, lim) for (leg, mv, _), lim in zip(legs, limits)], books

    async def _check_exits(self, wallets) -> None:
        now = datetime.now(timezone.utc)
        ids = [w.id for w in wallets]
        async with db.read() as s:
            pos = (await s.scalars(select(Position).where(Position.status == "open", Position.exit_rules.is_not(None),
                                                         Position.wallet_id.in_(ids)))).all()
        for p in pos:
            b = self.state.books.get(p.token_id)
            if b is None or b.best_bid is None or b.age() > settings.stale_seconds:
                continue
            r, bid = p.exit_rules, b.best_bid
            reason = None
            if bid >= p.entry_price + Decimal(str(r.get("take_profit", 99))):
                reason = "take_profit"
            elif bid <= p.entry_price - Decimal(str(r.get("stop_loss", 99))):
                reason = "stop_loss"
            elif now - p.opened_at > timedelta(minutes=r.get("max_hold_minutes", 10 ** 9)):
                reason = "time_stop"
            if reason and (p.wallet_id, -p.id) not in self.inflight:
                self.inflight.add((p.wallet_id, -p.id))
                asyncio.create_task(self._exit_guarded(p.id, reason))

    async def _exit_guarded(self, pos_id: int, reason: str) -> None:
        wid = None
        try:
            async with db.read() as s:
                p = await s.get(Position, pos_id)
                wid = p.wallet_id
            async with self.wallet_locks.setdefault(wid, asyncio.Lock()):
                async with db.read() as s:
                    w = await s.get(Wallet, wid)
                sc = scenario(w.scenario)
                await asyncio.sleep(sc.latency_ms / 1000)
                async with db.write() as s:
                    p, w = await s.get(Position, pos_id), await s.get(Wallet, wid)
                    if p.status != "open":
                        return
                    mv = self.state.markets.get(p.market_id)
                    b = self.state.books[p.token_id].copy()
                    rate = effective_fee_rate(mv.fee_rate, mv.fee_base_bps, sc) if mv else D0
                    f = walk(b.bid_levels(), p.shares, None, "SELL", sc, mv.tick if mv else Decimal("0.01"), rate)
                    if f.filled == 0:
                        return
                    await pf.record_sell(s, w, p, f, client_order_id=f"w{wid}:exit:{p.id}:{int(p.shares * 100)}",
                                         reason=reason, scenario=sc.name, latency_ms=sc.latency_ms, book=b.to_dict())
            self.dirty |= {"orders", "positions", "wallets"}
            await self.event("info", "trade", f"exit {reason} position {pos_id}", {"position": pos_id})
        except pf.DuplicateOrder:
            pass
        finally:
            await asyncio.sleep(10)
            if wid is not None:
                self.inflight.discard((wid, -pos_id))

    # ---------- settlement / equity ----------
    async def resolve(self) -> None:
        newly = await self.collector.check_resolutions()
        async with db.write() as s:
            mids = set((await s.scalars(select(Position.market_id).join(Market, Market.id == Position.market_id)
                                        .where(Position.status == "open", Market.resolved))).all()) | set(newly)
            settled = 0
            for mid in mids:
                m = await s.get(Market, mid)
                idx = {o.token_id: o.idx for o in (await s.scalars(select(Outcome).where(Outcome.market_id == mid))).all()}
                settled += await pf.settle_market(s, m, idx)
                if m.payouts:
                    await s.execute(update(Prediction).where(Prediction.market_id == mid, Prediction.outcome.is_(None))
                                    .values(outcome=float(m.payouts[0])))
        if newly:
            await self.collector.load_state()
            await self.collector.sync_subscriptions()
            self.dirty |= {"markets", "positions", "wallets", "predictions"}
        if settled:
            await self.event("info", "settlement", f"settled {settled} positions in {len(mids)} resolved markets")

    async def snapshot_equity(self) -> None:
        async with db.write() as s:
            for w in (await s.scalars(select(Wallet).where(Wallet.status != "archived"))).all():
                await pf.snapshot_equity(s, w, self.state.price_of)
                problems = await pf.integrity(s, w)
                if problems:
                    log.error("integrity %s: %s", w.name, problems)
                    self.loop_errors[f"integrity:{w.id}"] = "; ".join(problems)
        self.dirty.add("wallets")

    # ---------- ML ----------
    async def predict(self) -> None:
        n = await self.predictor.run(self.state)
        if n:
            self.dirty.add("predictions")

    async def maybe_train(self, force: bool = False) -> str:
        if self.train_proc and self.train_proc.returncode is None:
            await self._throttle_training()
            return "training already running"
        if not force:
            if not self.runtime.get("auto_train"):
                return "auto training disabled"
            async with db.read() as s:
                last = await s.scalar(select(TrainingRun.started_at).order_by(TrainingRun.id.desc()).limit(1))
            if last and datetime.now(timezone.utc) - last < timedelta(hours=self.runtime["retrain_hours"]):
                return "not due"
        free_mb = psutil.virtual_memory().available / 2 ** 20
        if free_mb < settings.min_free_mem_mb_for_training:
            await self.event("warning", "training", f"training postponed: only {free_mb:.0f} MB RAM free")
            return "postponed: memory pressure"
        async with db.write() as s:
            run = TrainingRun()
            s.add(run)
            await s.flush()
            run_id = run.id
        n = str(settings.ml_threads)
        env = os.environ | {"OMP_NUM_THREADS": n, "OPENBLAS_NUM_THREADS": n, "MKL_NUM_THREADS": n}
        self.train_proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.machine_learning.train",
                                                               str(run_id), env=env, cwd=str(BACKEND_DIR))
        asyncio.create_task(self._await_training(run_id))
        await self.event("info", "training", f"training run {run_id} started ({n} threads)")
        return f"started run {run_id}"

    async def _await_training(self, run_id: int) -> None:
        while self.train_proc.returncode is None:
            await self._throttle_training()
            try:
                await asyncio.wait_for(self.train_proc.wait(), 15)
            except asyncio.TimeoutError:
                pass
        self.dirty.add("models")
        await self.event("info" if self.train_proc.returncode == 0 else "error", "training",
                         f"training run {run_id} finished with code {self.train_proc.returncode}")

    async def _throttle_training(self) -> None:
        """Pause (SIGSTOP) the training process under memory pressure, resume (SIGCONT) when it recovers."""
        p = self.train_proc
        if not p or p.returncode is not None:
            return
        free_mb = psutil.virtual_memory().available / 2 ** 20
        if not self.train_paused and free_mb < settings.min_free_mem_mb_for_training / 2:
            p.send_signal(sig.SIGSTOP)
            self.train_paused = True
            await self.event("warning", "training", f"training paused: {free_mb:.0f} MB free")
        elif self.train_paused and free_mb > settings.min_free_mem_mb_for_training:
            p.send_signal(sig.SIGCONT)
            self.train_paused = False
            await self.event("info", "training", "training resumed")

    # ---------- housekeeping ----------
    async def retention(self) -> None:
        now = datetime.now(timezone.utc)
        days = int(self.runtime["retention_days"])
        async with db.write() as s:
            r1 = await s.execute(delete(Snapshot).where(Snapshot.ts_received < now - timedelta(days=days)))
            r2 = await s.execute(delete(Opportunity).where(Opportunity.status == "rejected",
                                                           Opportunity.last_seen < now - timedelta(days=max(days, 14))))
            await s.execute(delete(SystemEvent).where(SystemEvent.ts < now - timedelta(days=30)))
            await s.execute(delete(Prediction).where(Prediction.ts < now - timedelta(days=90)))
        async with db.engine().connect() as c:
            await c.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        await self.event("info", "retention", f"deleted {r1.rowcount} snapshots, {r2.rowcount} rejected opportunities")

    async def update_gates(self) -> None:
        """Recompute strategy statuses; status changes are stored as `gate` events (audit trail)."""
        from app.analytics import profitability as prof
        async with db.read() as s:
            rows = (await prof.analyze(s, self.state.price_of))["strategies"]
            changes = await prof.gate_changes(s, rows)
        if changes:
            async with db.write() as s:
                s.add_all(changes)
            self.dirty.add("validation")

    async def print_checks(self) -> None:
        if await run_print_checks(db, self.collector.client.http):
            self.dirty.add("validation")

    async def make_reports(self) -> None:
        now = datetime.now(timezone.utc)
        async with db.write() as s:
            for kind, a, b in await reports.due_periods(s, now):
                await reports.build(s, kind, a, b, time.time() - self.started)
                self.dirty.add("reports")

    async def sample_resources(self) -> None:
        proc = psutil.Process()
        rss = proc.memory_info().rss
        children = proc.children(recursive=True)
        child_rss = sum(c.memory_info().rss for c in children if c.is_running())
        vm = psutil.virtual_memory()
        try:
            db_size = settings.db_path.stat().st_size + (Path(str(settings.db_path) + "-wal").stat().st_size
                                                          if Path(str(settings.db_path) + "-wal").exists() else 0)
        except OSError:
            db_size = 0
        self.resources.append({"ts": datetime.now(timezone.utc).isoformat(), "rss_mb": rss / 2 ** 20,
                               "training_rss_mb": child_rss / 2 ** 20, "cpu_pct": proc.cpu_percent(None),
                               "sys_mem_avail_mb": vm.available / 2 ** 20, "db_mb": db_size / 2 ** 20,
                               "markets": len(self.state.markets), "scan_ms": self.last_scan_ms})

    def health(self) -> dict:
        f = self.state.feed
        return {"ws_connected": f.connected, "feed_healthy": self.state.healthy(),
                "seconds_since_message": time.time() - f.last_message if f.last_message else None,
                "reconnects": f.reconnects, "messages": f.messages, "crypto_fresh": self.state.crypto.fresh(),
                "markets": len(self.state.markets), "tokens": len(self.state.books),
                "last_discovery": f.last_discovery, "uptime_s": time.time() - self.started,
                "trading_halted": not self.state.healthy() or bool(self.runtime.get("kill_switch")),
                "kill_switch": bool(self.runtime.get("kill_switch")), "loop_errors": self.loop_errors,
                "training_running": bool(self.train_proc and self.train_proc.returncode is None),
                "training_paused": self.train_paused, "profile": settings.resource_profile,
                "real_trading": False}


engine = Engine()
