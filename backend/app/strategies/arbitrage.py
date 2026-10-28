"""Arbitrage strategies. Each one only marks a signal `guaranteed` when the payoff floor is proven from
market structure; everything else is logged as speculative and never traded by these strategies."""
import re
from decimal import Decimal

from app.strategies.base import Context, Strategy, arb_signal

D = Decimal


def _binary(m) -> bool:
    return len(m.tokens) == 2


class YesNoArbitrage(Strategy):
    name = "arb_yes_no"
    family = "arbitrage"
    description = ("Buy both outcomes of a binary market when ask(YES)+ask(NO)+fees < 1. "
                   "Payoff is exactly 1 per pair under every resolution, including 50-50.")
    defaults = {"trade_usdc": 5, "min_net_edge": 0.005}

    def evaluate(self, ctx: Context, cfg: dict):
        out = []
        for m in ctx.markets.values():
            if not _binary(m) or m.closed or not m.accepting_orders:
                continue
            s = arb_signal(self.name, ctx, f"{self.name}:{m.id}", m.question, m, [(m, 0), (m, 1)], D(1), cfg, True,
                           {"structure": "binary complement"})
            if s:
                out.append(s)
        return out


class MultiOutcomeArbitrage(Strategy):
    name = "arb_multi"
    family = "arbitrage"
    description = ("negRisk events (mutually exclusive outcomes). Buy every YES when sum(asks) < 1 — only "
                   "guaranteed if the set is provably exhaustive. Buy every NO when sum(asks) < n-1 — guaranteed "
                   "by mutual exclusivity alone (at most one YES).")
    defaults = {"trade_usdc": 5, "min_net_edge": 0.005, "max_legs": 12}

    def evaluate(self, ctx: Context, cfg: dict):
        out = []
        for eid, ms in ctx.events.items():
            ms = [m for m in ms if _binary(m) and not m.closed]
            if len(ms) < 2 or len(ms) > cfg.get("max_legs", 12) or not all(m.neg_risk for m in ms):
                continue
            head, n = ms[0], len(ms)
            exhaustive = all(m.event_exhaustive for m in ms)
            title = head.event_title or head.question
            s = arb_signal(self.name, ctx, f"{self.name}:{eid}:yes", f"{title} [all YES x{n}]", head,
                           [(m, 0) for m in ms], D(1), cfg, exhaustive,
                           {"structure": "all YES", "exhaustive_proven": exhaustive, "legs": n})
            if s:
                out.append(s)
            s = arb_signal(self.name, ctx, f"{self.name}:{eid}:no", f"{title} [all NO x{n}]", head,
                           [(m, 1) for m in ms], D(n - 1), cfg, True,
                           {"structure": "all NO", "legs": n, "note": "payoff >= n-1 by mutual exclusivity"})
            if s:
                out.append(s)
        return out


_NUM = re.compile(r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*([kKmMbB])?\b")


def parse_threshold(question: str) -> tuple[str, float] | None:
    """'Will the price of Bitcoin be above $72,000 on ...?' -> ('above', 72000.0)."""
    q = question.lower()
    for word, direction in (("above", "above"), ("greater than", "above"), ("higher than", "above"),
                            ("below", "below"), ("less than", "below"), ("lower than", "below")):
        i = q.find(word)
        if i >= 0:
            mm = _NUM.search(question, i + len(word))
            if not mm:
                return None
            v = float(mm.group(1).replace(",", ""))
            v *= {"k": 1e3, "m": 1e6, "b": 1e9}.get((mm.group(2) or "").lower(), 1)
            return direction, v
    return None


class RelatedMarkets(Strategy):
    name = "related_markets"
    family = "arbitrage"
    description = ("Threshold ladders in one event ('above 95k' and 'above 100k'). If A implies B, "
                   "YES(B)+NO(A) pays >= 1 in every state. Guaranteed only for same event, same end date and "
                   "same resolution rule; other relations are correlation, not arbitrage.")
    defaults = {"trade_usdc": 5, "min_net_edge": 0.005}

    def evaluate(self, ctx: Context, cfg: dict):
        out = []
        for eid, ms in ctx.events.items():
            ladder = []
            for m in ms:
                t = parse_threshold(m.question) if _binary(m) and not m.closed else None
                if t:
                    ladder.append((t, m))
            for (ta, a) in ladder:
                for (tb, b) in ladder:
                    if a is b or ta[0] != tb[0]:
                        continue
                    # A => B: for 'above', higher threshold implies lower; for 'below', lower implies higher
                    implies = ta[1] > tb[1] if ta[0] == "above" else ta[1] < tb[1]
                    if not implies:
                        continue
                    same_rule = a.end_date == b.end_date and _rule_template(a) == _rule_template(b)
                    s = arb_signal(self.name, ctx, f"{self.name}:{a.id}:{b.id}",
                                   f"{b.question} YES + {a.question} NO", b, [(b, 0), (a, 1)], D(1), cfg, same_rule,
                                   {"implies": f"{a.id} => {b.id}", "thresholds": [ta[1], tb[1]],
                                    "same_resolution_rule": same_rule,
                                    "relation": "logical" if same_rule else "correlation"})
                    if s:
                        out.append(s)
        return out


def _rule_template(m) -> str:
    """Resolution text with numbers removed: equal templates => same source, time and comparison."""
    return re.sub(r"[\d,.$]+", "#", m.description or "")
