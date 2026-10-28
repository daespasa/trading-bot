from decimal import Decimal as D

from app.paper_trading.execution import (SCENARIOS, buy_cost, max_equal_shares, shares_for_budget, taker_fee,
                                         walk)

OPT, BASE, CONS = SCENARIOS["OPTIMISTIC"], SCENARIOS["REALISTIC"], SCENARIOS["CONSERVATIVE"]
T = D("0.01")


def test_fee_formula_matches_docs():
    # docs example: 100 shares of a crypto market at 0.50 -> $1.75
    assert taker_fee(D(100), D("0.5"), D("0.07")) == D("1.75000")
    assert taker_fee(D(100), D("0.5"), D(0)) == 0
    assert taker_fee(D("0.01"), D("0.99"), D("0.04")) == D("0.00001")  # minimum charge


def test_walk_buys_from_asks_and_respects_limit():
    asks = [(D("0.47"), D(10)), (D("0.48"), D(10)), (D("0.60"), D(100))]
    f = walk(asks, D(15), D("0.50"), "BUY", OPT, T, D(0))
    assert f.filled == 15 and f.notional == D("0.47") * 10 + D("0.48") * 5
    assert f.status == "filled"
    assert f.slippage == f.avg_price - D("0.47")
    f = walk(asks, D(50), D("0.50"), "BUY", OPT, T, D(0))
    assert f.filled == 20 and f.status == "partial"  # 0.60 level is above the limit


def test_scenarios_get_progressively_worse():
    asks = [(D("0.47"), D(10)), (D("0.49"), D(100))]
    costs = [buy_cost(asks, D(10), sc, T, D("0.07")) for sc in (OPT, BASE, CONS)]
    assert costs[0] < costs[1] < costs[2]
    # conservative only gets half the displayed depth and pays one tick more
    f = walk(asks, D(10), None, "BUY", CONS, T, D(0))
    assert f.fills[0] == ["0.48", "5.00"]


def test_sell_walks_bids_downward():
    bids = [(D("0.55"), D(5)), (D("0.50"), D(5))]
    f = walk(bids, D(8), None, "SELL", OPT, T, D(0))
    assert f.notional == D("0.55") * 5 + D("0.50") * 3
    assert f.cash_delta == f.notional  # no fee
    assert f.slippage > 0


def test_insufficient_depth_returns_none():
    assert buy_cost([(D("0.5"), D(3))], D(5), OPT, T, D(0)) is None


def test_max_equal_shares_for_yes_no_arb():
    yes = [(D("0.47"), D(100))]
    no = [(D("0.50"), D(100))]
    q, cost = max_equal_shares([(yes, T, D(0)), (no, T, D(0))], D(5), OPT, D(1), D("0.005"), D(5))
    assert q == D("5.15") and cost <= 5  # 5 / 0.97
    assert (q - cost) / cost >= D("0.005")
    # with crypto fees the 3c edge is mostly eaten; demand 3% net -> nothing qualifies
    q, _ = max_equal_shares([(yes, T, D("0.07")), (no, T, D("0.07"))], D(5), OPT, D(1), D("0.03"), D(5))
    assert q == 0


def test_min_order_size_enforced():
    yes = [(D("0.47"), D(3))]
    no = [(D("0.50"), D(3))]
    q, _ = max_equal_shares([(yes, T, D(0)), (no, T, D(0))], D(5), OPT, D(1), D(0), D(5))
    assert q == 0


def test_shares_for_budget():
    asks = [(D("0.40"), D(5)), (D("0.50"), D(100))]
    q = shares_for_budget(asks, D(5), D("0.55"), OPT, T, D(0))
    assert q == D("11.00")  # 5*0.4 + 6*0.5 = 5.00 exactly
    f = walk(asks, q + D("0.01"), D("0.55"), "BUY", OPT, T, D(0))
    assert f.notional > 5
