"""Training job. Runs as a separate, niced process: `python -m app.machine_learning.train <run_id>`.

Walk-forward protocol: observation times are split into K consecutive test windows over the latest half
of the data. For a window starting at time a, the training set is every row whose market *resolved*
before a (its label was knowable at a). Calibration (Platt) uses the most recent 20% of that training set.
A model becomes ACTIVE only if, out of sample, it beats the market price on log loss with a
market-clustered t-stat > 2, is calibrated (ECE < 0.05), and the simple trading rule is profitable after
assumed costs on >= 30 trades. Everything else is reported, never hidden.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models import MLModel, TrainingRun
from app.machine_learning.dataset import build_history_dataset, build_live_dataset
from app.machine_learning.features import FEATURE_SETS
from app.machine_learning.metrics import calibration_bins, clustered_edge, prob_metrics, row_logloss
from app.paper_trading.execution import CATEGORY_FEE_RATES

log = logging.getLogger("train")

GATE = {"min_rows": 600, "min_markets": 120, "min_test_rows": 300, "min_test_markets": 60, "min_t": 2.0,
        "max_ece": 0.05, "min_sim_trades": 30, "folds": 4, "edge_margin": 0.05, "half_spread": 0.01}


def make_model(kind: str, threads: int):
    if kind == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    if kind == "lightgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=40,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                              n_jobs=threads, verbose=-1)
    if kind == "xgboost":  # optional, only if installed
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=4, subsample=0.8, n_jobs=threads)
    raise ValueError(kind)


def fit_calibrated(kind: str, X: pd.DataFrame, y: np.ndarray, order: np.ndarray, threads: int):
    """Fit on the older 80% (by resolution time), Platt-calibrate on the newest 20%."""
    idx = np.argsort(order)
    cut = int(len(idx) * 0.8)
    fit_i, cal_i = idx[:cut], idx[cut:]
    model = make_model(kind, threads).fit(X.iloc[fit_i], y[fit_i])
    if len(np.unique(y[cal_i])) < 2:
        return model
    return CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid").fit(X.iloc[cal_i], y[cal_i])


def simulate_rule(test: pd.DataFrame, p_model: np.ndarray) -> dict:
    """First qualifying observation per market: buy YES if p_model - p > margin, NO if p - p_model > margin.
    Cost = price + assumed half spread + category taker fee. Approximate: history prices are not quotes."""
    m, hs = GATE["edge_margin"], GATE["half_spread"]
    pnl, cost_sum, n, done = 0.0, 0.0, 0, set()
    for _, row in test.assign(pm=p_model).sort_values("t_obs", kind="stable").iterrows():
        pm = row["pm"]
        if row["market_id"] in done:
            continue
        p = row["p"]
        side = 1 if pm - p > m else (0 if p - pm > m else None)
        if side is None:
            continue
        price = min((p if side else 1 - p) + hs, 0.99)
        rate = float(CATEGORY_FEE_RATES.get(row["category"], CATEGORY_FEE_RATES["other"]))
        c = price + rate * price * (1 - price)
        win = row["y"] if side else 1 - row["y"]
        pnl += win - c
        cost_sum += c
        n += 1
        done.add(row["market_id"])
    return {"trades": n, "pnl_per_share_set": pnl, "roi": pnl / cost_sum if cost_sum else None}


def walk_forward(df: pd.DataFrame, feats: list[str], kind: str, threads: int) -> dict:
    df = df.sort_values("t_obs").reset_index(drop=True)
    times = df["t_obs"].values
    edges = np.quantile(times, np.linspace(0.5, 1.0, GATE["folds"] + 1))
    preds, tests, folds = [], [], []
    for k in range(GATE["folds"]):
        a, b = edges[k], edges[k + 1]
        test = df[(times >= a) & ((times < b) if k < GATE["folds"] - 1 else (times <= b))]
        train = df[df["closed_ts"] < a]  # labels known strictly before the window starts
        if len(test) == 0 or len(train) < 200 or train["y"].nunique() < 2:
            folds.append({"fold": k, "skipped": True, "train": len(train), "test": len(test)})
            continue
        model = fit_calibrated(kind, train[feats], train["y"].values, train["closed_ts"].values, threads)
        p = model.predict_proba(test[feats])[:, 1]
        preds.append(p)
        tests.append(test)
        folds.append({"fold": k, "train": len(train), "test": len(test), "start": float(a), "end": float(b),
                      "model": prob_metrics(test["y"], p), "market": prob_metrics(test["y"], test["p"])})
    if not preds:
        return {"folds": folds, "n_test": 0}
    p = np.concatenate(preds)
    test = pd.concat(tests)
    y = test["y"].values
    return {
        "folds": folds, "n_test": int(len(test)), "n_test_markets": int(test["market_id"].nunique()),
        "model": prob_metrics(y, p), "market": prob_metrics(y, test["p"].values),
        "edge": clustered_edge(row_logloss(y, test["p"].values), row_logloss(y, p), test["market_id"].values),
        "calibration_model": calibration_bins(y, p), "calibration_market": calibration_bins(y, test["p"].values),
        "simulation": simulate_rule(test, p),
    }


def decide(df: pd.DataFrame, ev: dict) -> tuple[str, str]:
    if len(df) < GATE["min_rows"] or df["market_id"].nunique() < GATE["min_markets"] \
            or ev.get("n_test", 0) < GATE["min_test_rows"] or ev.get("n_test_markets", 0) < GATE["min_test_markets"]:
        return "INSUFFICIENT TRAINING DATA", (f"rows={len(df)} markets={df['market_id'].nunique() if len(df) else 0} "
                                              f"test_rows={ev.get('n_test', 0)}; need {GATE['min_rows']}/"
                                              f"{GATE['min_markets']}/{GATE['min_test_rows']}")
    fails = []
    e, sim = ev["edge"], ev["simulation"]
    if not e.get("t") or e["t"] < GATE["min_t"]:
        fails.append(f"log-loss gain vs market {e['delta_logloss']:+.4f} (t={e.get('t') or 0:.2f}) not significant")
    if ev["model"]["ece"] > GATE["max_ece"]:
        fails.append(f"ECE {ev['model']['ece']:.3f} > {GATE['max_ece']}")
    if sim["trades"] < GATE["min_sim_trades"] or not sim["roi"] or sim["roi"] <= 0:
        fails.append(f"simulated rule: {sim['trades']} trades, ROI {sim['roi']}")
    if fails:
        return "NO DEMONSTRATED PREDICTIVE EDGE", "; ".join(fails)
    return "ACTIVE", "passed out-of-sample gate"


def _engine():
    eng = create_engine(f"sqlite:///{settings.db_path}", connect_args={"timeout": 30})
    event.listen(eng, "connect", lambda c, _: c.execute("PRAGMA busy_timeout=30000"))
    return eng


def run(run_id: int | None, kinds=("logistic", "lightgbm")) -> list[dict]:
    threads = settings.ml_threads
    eng = _engine()
    ml_dir = settings.data_dir / "ml"
    results = []
    try:
        hist = build_history_dataset(ml_dir / "history.csv")
        live = build_live_dataset(settings.db_path)
        sets = {"core": hist, "book": live}
        try:
            import xgboost  # noqa: F401
            kinds = tuple(kinds) + ("xgboost",)
        except ImportError:
            pass
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for fs, df in sets.items():
            feats = FEATURE_SETS[fs]
            for kind in kinds:
                ev = walk_forward(df, feats, kind, threads) if len(df) >= 200 else {"n_test": 0}
                status, reason = decide(df, ev)
                path = None
                if status != "INSUFFICIENT TRAINING DATA":
                    final = fit_calibrated(kind, df[feats], df["y"].values, df["closed_ts"].values, threads)
                    path = str(settings.models_dir / f"{kind}-{fs}-{version}.joblib")
                    joblib.dump({"model": final, "features": feats, "kind": kind, "feature_set": fs}, path)
                n_train = int(len(df))
                with Session(eng) as s:
                    s.add(MLModel(kind=kind, version=version, feature_set=fs, path=path, status=status, reason=reason,
                                  params={"gate": GATE, "threads": threads}, metrics=ev, n_train=n_train,
                                  n_test=ev.get("n_test", 0), training_run_id=run_id))
                    s.commit()
                results.append({"kind": kind, "feature_set": fs, "status": status, "reason": reason})
                log.info("%s/%s: %s (%s)", kind, fs, status, reason)
        _finish(eng, run_id, "done", json.dumps(results),
                {"history_rows": len(hist), "history_markets": int(hist["market_id"].nunique()) if len(hist) else 0,
                 "live_rows": len(live)})
    except Exception as e:
        log.exception("training failed")
        _finish(eng, run_id, "failed", repr(e), {})
        raise
    return results


def _finish(eng, run_id, status, message, dataset):
    if run_id is None:
        return
    with Session(eng) as s:
        r = s.get(TrainingRun, run_id)
        if r:
            r.status, r.message, r.dataset, r.finished_at = status, message[:4000], dataset, datetime.now(timezone.utc)
            s.commit()


if __name__ == "__main__":
    from app.core.logging import setup_logging
    setup_logging()
    try:
        os.nice(10)
    except OSError:
        pass
    run(int(sys.argv[1]) if len(sys.argv) > 1 else None)
