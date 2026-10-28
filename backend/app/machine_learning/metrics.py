import math

import numpy as np


def ece(y, p, bins: int = 10) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(y[idx == b].mean() - p[idx == b].mean()) * (idx == b).sum() for b in range(bins)
                     if (idx == b).any()) / len(y))


def calibration_bins(y, p, bins: int = 10) -> list[dict]:
    y, p = np.asarray(y, float), np.asarray(p, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return [{"bin": (b + 0.5) / bins, "mean_pred": float(p[idx == b].mean()), "frac_pos": float(y[idx == b].mean()),
             "count": int((idx == b).sum())} for b in range(bins) if (idx == b).any()]


def prob_metrics(y, p) -> dict:
    """Probability-quality metrics. Accuracy is reported but never used alone to judge a model."""
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    if len(y) == 0:
        return {}
    pred = p >= 0.5
    tp = float((pred & (y == 1)).sum())
    out = {
        "n": int(len(y)),
        "log_loss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
        "brier": float(((p - y) ** 2).mean()),
        "ece": ece(y, p),
        "accuracy": float((pred == (y == 1)).mean()),
        "precision": tp / pred.sum() if pred.sum() else None,
        "recall": tp / (y == 1).sum() if (y == 1).sum() else None,
        "base_rate": float(y.mean()),
    }
    return out


def clustered_edge(ll_market, ll_model, groups) -> dict:
    """Mean per-row log-loss improvement of model over market, with SE clustered by market
    (rows of one market are not independent). t > 2 ~ significant at 5% one-sided-ish."""
    d = np.asarray(ll_market) - np.asarray(ll_model)
    g = np.asarray(groups)
    means = np.array([d[g == k].mean() for k in np.unique(g)])
    n = len(means)
    if n < 2:
        return {"delta_logloss": float(d.mean()) if len(d) else 0.0, "se": None, "t": None, "clusters": n}
    se = float(means.std(ddof=1) / math.sqrt(n))
    m = float(means.mean())
    return {"delta_logloss": m, "se": se, "t": m / se if se > 0 else None, "clusters": n}


def row_logloss(y, p):
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))
