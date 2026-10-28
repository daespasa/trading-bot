"""ML tests. SYNTHETIC data is used here only to test the pipeline mechanics; it is never stored or shown."""
import numpy as np
import pandas as pd

from app.machine_learning import train
from app.machine_learning.features import CORE, core_features
from app.machine_learning.metrics import clustered_edge, ece, prob_metrics


def test_features_ignore_future_points():
    series = [(i * 3600.0, 0.5 + 0.01 * i) for i in range(48)]
    t_obs, end = 30 * 3600.0, 100 * 3600.0
    f1 = core_features(series, t_obs, end, "crypto")
    future_changed = series[:31] + [(t, 0.99) for t, _ in series[31:]]
    assert core_features(future_changed, t_obs, end, "crypto") == f1
    assert f1["p"] == series[30][1]
    assert core_features(series, end + 1, end, "crypto") is None  # after scheduled end: no row


def _synthetic(n_markets=400, signal=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(n_markets):
        truth = rng.uniform(0.05, 0.95)
        y = int(rng.uniform() < truth)
        closed = 1_000_000 + m * 3600
        for h in (48, 24, 6):
            p = float(np.clip(truth + rng.normal(0, 0.05), 0.01, 0.99))
            f = {c: 0.0 for c in CORE}
            f.update(p=p, logit_p=np.log(p / (1 - p)), dp_1h=signal * (y - 0.5) + rng.normal(0, 0.01))
            rows.append({"market_id": str(m), "event_id": "", "t_obs": closed - h * 3600, "closed_ts": closed,
                         "category": "other", "y": y, **f})
    return pd.DataFrame(rows)


def test_walk_forward_trains_only_on_resolved_before_window():
    df = _synthetic()
    ev = train.walk_forward(df, CORE, "logistic", 1)
    for f in ev["folds"]:
        if f.get("skipped"):
            continue
        assert (df[df["closed_ts"] < f["start"]]["t_obs"] < f["start"]).all()
    assert ev["n_test"] > 0


def test_no_signal_model_does_not_activate():
    df = _synthetic(signal=0.0)
    ev = train.walk_forward(df, CORE, "lightgbm", 1)
    status, _ = train.decide(df, ev)
    assert status == "NO DEMONSTRATED PREDICTIVE EDGE"


def test_insufficient_data_status():
    df = _synthetic(n_markets=30)
    status, _ = train.decide(df, {"n_test": 0})
    assert status == "INSUFFICIENT TRAINING DATA"


def test_metrics():
    y = np.array([0, 1, 1, 0])
    m = prob_metrics(y, np.array([0.1, 0.9, 0.8, 0.3]))
    assert m["accuracy"] == 1.0 and m["brier"] < 0.05 and m["log_loss"] < 0.3
    assert ece([1, 0], [0.5, 0.5]) == 0.0
    e = clustered_edge([1.0, 1.0, 1.0, 1.0], [0.5, 0.6, 0.4, 0.5], ["a", "a", "b", "b"])
    assert e["delta_logloss"] > 0 and e["clusters"] == 2
