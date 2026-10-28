"""Feature construction. Every feature at observation time t uses only points with timestamp <= t.
`test_ml.py` asserts this by perturbing the future and checking features are unchanged."""
import math

CATS = ("crypto", "sports", "politics")
CORE = ["p", "logit_p", "dp_1h", "dp_6h", "dp_24h", "vol_24h", "range_24h", "log_hours_left", "log_age_hours",
        "n_obs_24h"] + [f"cat_{c}" for c in CATS] + ["cat_other"]
BOOK = CORE + ["spread", "imbalance", "log_depth"]
FEATURE_SETS = {"core": CORE, "book": BOOK}
MAX_STALENESS_S = 6 * 3600


def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


def core_features(series: list[tuple[float, float]], t_obs: float, end_ts: float, category: str) -> dict | None:
    """series: [(epoch_s, probability)] ascending (may include the future; it is cut here)."""
    pts = [(t, p) for t, p in series if t <= t_obs]
    if len(pts) < 3 or t_obs - pts[-1][0] > MAX_STALENESS_S or end_ts <= t_obs:
        return None
    p = pts[-1][1]

    def at(hours: float) -> float:
        cut = t_obs - hours * 3600
        prev = [q for t, q in pts if t <= cut]
        return prev[-1] if prev else pts[0][1]

    last24 = [q for t, q in pts if t >= t_obs - 86400]
    diffs = [b - a for a, b in zip(last24, last24[1:])]
    vol = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if diffs else 0.0
    f = {
        "p": p, "logit_p": _logit(p), "dp_1h": p - at(1), "dp_6h": p - at(6), "dp_24h": p - at(24),
        "vol_24h": vol, "range_24h": (max(last24) - min(last24)) if last24 else 0.0,
        "log_hours_left": math.log1p((end_ts - t_obs) / 3600), "log_age_hours": math.log1p((t_obs - pts[0][0]) / 3600),
        "n_obs_24h": float(len(last24)),
    }
    for c in CATS:
        f[f"cat_{c}"] = 1.0 if category == c else 0.0
    f["cat_other"] = 1.0 if category not in CATS else 0.0
    return f


def book_features(best_bid: float | None, best_ask: float | None, bid_depth: float | None,
                  ask_depth: float | None) -> dict | None:
    if best_bid is None or best_ask is None:
        return None
    bd, ad = bid_depth or 0.0, ask_depth or 0.0
    return {"spread": best_ask - best_bid, "imbalance": (bd - ad) / (bd + ad) if bd + ad > 0 else 0.0,
            "log_depth": math.log1p(bd + ad)}
