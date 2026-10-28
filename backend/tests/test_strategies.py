from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.market_data.book import Book
from app.market_data.collector import MarketView
from app.strategies import REGISTRY
from app.strategies.arbitrage import parse_threshold
from app.strategies.base import Context
from app.strategies.rules import prob_above, prob_twap_above

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def mv(mid, question, tokens=("y", "n"), event=None, fee=D(0), neg_risk=False, exhaustive=False, desc="rule",
       end=NOW + timedelta(days=2), start=None):
    return MarketView(mid, question, event, "Event", None, "other", neg_risk, exhaustive, end, start, fee > 0, fee, 0,
                      D("0.01"), D(5), True, False, mid, desc, list(tokens), ["Yes", "No"])


def book(tok, asks, bids=()):
    b = Book(tok)
    b.replace([{"price": p, "size": s} for p, s in bids], [{"price": p, "size": s} for p, s in asks], None, "test")
    return b


def ctx(markets, books, **kw):
    ev = {}
    for m in markets:
        if m.event_id:
            ev.setdefault(m.event_id, []).append(m)
    return Context(NOW, {m.id: m for m in markets}, books, ev, kw.pop("history", {}), **kw)


def test_parse_threshold():
    assert parse_threshold("Will the price of Bitcoin be above $72,000 on October 29?") == ("above", 72000.0)
    assert parse_threshold("Will ETH be below $3.5k?") == ("below", 3500.0)
    assert parse_threshold("Who wins?") is None


def _fresh(*bs):
    for b in bs:
        b.ts_received = NOW.timestamp()
    return {b.token_id: b for b in bs}


def test_yes_no_arb_detected_and_fees_matter():
    m = mv("m1", "Q?", ("y", "n"))
    books = _fresh(book("y", [("0.47", "100")]), book("n", [("0.50", "100")]))
    [s] = REGISTRY["arb_yes_no"].evaluate(ctx([m], books), {"trade_usdc": 5, "min_net_edge": 0.005})
    assert s.actionable and s.guaranteed and abs(s.gross_edge - 0.03 / 0.97) < 1e-9
    m.fee_rate = D("0.07")  # crypto fees eat the 3c edge: logged, not actionable
    [s] = REGISTRY["arb_yes_no"].evaluate(ctx([m], books), {"trade_usdc": 5, "min_net_edge": 0.03})
    assert not s.actionable and "below" in s.reason


def test_no_arb_when_sum_at_least_one():
    m = mv("m1", "Q?")
    books = _fresh(book("y", [("0.50", "100")]), book("n", [("0.51", "100")]))
    assert REGISTRY["arb_yes_no"].evaluate(ctx([m], books), {"trade_usdc": 5}) == []


def test_stale_book_ignored():
    m = mv("m1", "Q?")
    books = _fresh(book("y", [("0.47", "100")]), book("n", [("0.50", "100")]))
    books["y"].ts_received = NOW.timestamp() - 1000
    assert REGISTRY["arb_yes_no"].evaluate(ctx([m], books), {"trade_usdc": 5}) == []


def test_multi_outcome_requires_exhaustiveness_for_all_yes():
    ms = [mv(f"m{i}", f"Candidate {i}?", (f"y{i}", f"n{i}"), event="e", neg_risk=True, exhaustive=False) for i in range(3)]
    books = _fresh(*[book(f"y{i}", [("0.30", "100")]) for i in range(3)],
                   *[book(f"n{i}", [("0.71", "100")]) for i in range(3)])
    sigs = REGISTRY["arb_multi"].evaluate(ctx(ms, books), {"trade_usdc": 5, "min_net_edge": 0.005, "max_legs": 12})
    yes = [s for s in sigs if s.key.endswith(":yes")][0]
    assert not yes.guaranteed and not yes.actionable  # 0.90 < 1 but not provably exhaustive
    for m in ms:
        m.event_exhaustive = True
    sigs = REGISTRY["arb_multi"].evaluate(ctx(ms, books), {"trade_usdc": 5, "min_net_edge": 0.005, "max_legs": 12})
    yes = [s for s in sigs if s.key.endswith(":yes")][0]
    assert yes.guaranteed and yes.actionable
    assert not [s for s in sigs if s.key.endswith(":no")]  # 3*0.71 = 2.13 > n-1 = 2


def test_related_markets_ladder():
    hi = mv("hi", "Will Bitcoin be above $100,000 on Oct 29?", ("yh", "nh"), event="e", desc="close above $100,000")
    lo = mv("lo", "Will Bitcoin be above $95,000 on Oct 29?", ("yl", "nl"), event="e", desc="close above $95,000")
    # inconsistent: P(>100k) priced higher than P(>95k)
    books = _fresh(book("yl", [("0.40", "100")]), book("nl", [("0.62", "100")]),
                   book("yh", [("0.45", "100")]), book("nh", [("0.50", "100")]))
    sigs = REGISTRY["related_markets"].evaluate(ctx([hi, lo], books), {"trade_usdc": 5, "min_net_edge": 0.005})
    [s] = sigs
    assert s.guaranteed and s.actionable
    assert [leg.token_id for leg in s.legs] == ["yl", "nh"]  # YES(95k) + NO(100k) costs 0.90, pays >= 1


def test_prob_above_sanity():
    assert abs(prob_above(100, 100, 0.001, 60) - 0.5) < 0.01
    assert prob_above(110, 100, 0.001, 60) > 0.99
    assert prob_above(90, 100, 0.001, 60) < 0.01
    assert prob_above(100, 100, 0.001, 60, drift_min=0.0005) > 0.6


def test_mean_reversion_signal_after_drop():
    m = mv("m1", "Q?", end=NOW + timedelta(days=5))
    t = NOW.timestamp()
    hist = {"y": deque([(t - 1200, 0.60), (t - 600, 0.55), (t - 10, 0.45)]), "n": deque()}
    books = _fresh(book("y", [("0.46", "100")], [("0.44", "100")]), book("n", [("0.56", "100")], [("0.54", "100")]))
    cfg = dict(REGISTRY["mean_reversion"].defaults)
    [s] = REGISTRY["mean_reversion"].evaluate(ctx([m], books, history=hist), cfg)
    assert s.legs[0].token_id == "y" and s.actionable and s.exit_rules["take_profit"] == 0.04


def test_twap_probability_uses_elapsed_average():
    # 2/3 of the window averaged 1% above the strike: spot at the strike cannot drag the TWAP below it
    assert prob_twap_above(100, 100, 101, 2 / 3, 0.0005, 5) > 0.99
    # same spot, nothing elapsed yet: ~coin flip
    assert abs(prob_twap_above(100, 100, None, 0.0, 0.0005, 15) - 0.5) < 0.02
    # window finished: deterministic
    assert prob_twap_above(100, 100, 99.9, 1.0, 0.0005, 0) == 0.0
