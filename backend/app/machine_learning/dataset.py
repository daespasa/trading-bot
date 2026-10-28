"""Training datasets from real data only.

history: resolved Polymarket binary markets (Gamma) + their CLOB price history. One row per market per
  horizon h hours before the *scheduled* end date, kept only if the market had not yet closed at that time.
  The price history is last-trade/mid based, not an executable quote: book features are absent here.
live: snapshots this app recorded for markets that later resolved (adds spread/imbalance/depth).
Label y = 1 if outcome index 0 (YES/Up) paid 1. 50-50 resolutions are dropped.
"""
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from app.core.config import settings
from app.machine_learning.features import book_features, core_features
from app.market_data.polymarket import category_of

log = logging.getLogger(__name__)
HORIZONS_H = (1, 3, 6, 12, 24, 48, 96, 168)
SKIP_SLUG = ("updown-5m", "updown-15m", "updown-1h", "-up-or-down-")  # sub-hour markets: hourly history is useless


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()  # DB stores naive UTC
    except ValueError:
        return None


def _get(http: httpx.Client, url: str, params: dict):
    for i in range(4):
        try:
            r = http.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            client_err = isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500 \
                and e.response.status_code != 429
            if i == 3 or client_err:
                raise
            time.sleep(2 ** i)


def build_history_dataset(path: Path, pages: int = 20, min_volume: float = 5000, page_size: int = 100) -> pd.DataFrame:
    """Incrementally append rows for newly resolved markets to `path` (CSV). Returns the full dataset."""
    old = pd.read_csv(path, dtype={"market_id": str, "event_id": str}) if path.exists() else pd.DataFrame()
    seen = set(old["market_id"]) if len(old) else set()
    rows = []
    with httpx.Client(timeout=20, headers={"User-Agent": "polymarket-paper-lab/1.0"}) as http:
        for page in range(pages):
            ms = _get(http, f"{settings.gamma_url}/markets",
                      {"closed": "true", "order": "closedTime", "ascending": "false", "limit": page_size,
                       "offset": page * page_size, "volume_num_min": min_volume})
            if not ms:
                break
            for m in ms:
                mid = str(m["id"])
                if mid in seen or any(k in (m.get("slug") or "") for k in SKIP_SLUG):
                    continue
                seen.add(mid)
                try:
                    prices = json.loads(m.get("outcomePrices") or "[]")
                    toks = json.loads(m.get("clobTokenIds") or "[]")
                except ValueError:
                    continue
                if len(toks) != 2 or m.get("umaResolutionStatus") != "resolved" or sorted(prices) != ["0", "1"]:
                    continue
                end_ts, closed_ts = _ts(m.get("endDate")), _ts(m.get("closedTime"))
                if not end_ts or not closed_ts:
                    continue
                last = int(min(end_ts, closed_ts))  # no observation is ever later than this
                try:  # the API rejects windows longer than ~2 weeks; features need 8 days at most
                    hist = _get(http, f"{settings.clob_url}/prices-history",
                                {"market": toks[0], "startTs": last - 8 * 86400, "endTs": last, "fidelity": 60})
                except httpx.HTTPError as e:
                    log.warning("skipping market %s: %s", mid, e)
                    continue
                series = [(float(h["t"]), float(h["p"])) for h in (hist or {}).get("history", [])]
                if len(series) < 5:
                    continue
                cat = category_of(m, (m.get("events") or [None])[0])
                ev = (m.get("events") or [{}])[0]
                for h in HORIZONS_H:
                    t_obs = end_ts - h * 3600
                    if t_obs >= closed_ts:  # market already over at that time: not tradable
                        continue
                    f = core_features(series, t_obs, end_ts, cat)
                    if f:
                        rows.append({"market_id": mid, "event_id": str(ev.get("id", "")), "t_obs": t_obs,
                                     "closed_ts": closed_ts, "category": cat, "y": int(prices[0] == "1"),
                                     "source": "history", **f})
    df = pd.concat([old, pd.DataFrame(rows)], ignore_index=True) if rows else old
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    log.info("history dataset: %d new rows, %d total", len(rows), len(df))
    return df


def build_live_dataset(db_path: Path, step_s: int = 3600) -> pd.DataFrame:
    """Rows from our own snapshots of markets that resolved (YES token only, hourly samples)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        mk = con.execute("SELECT m.id, m.event_id, m.category, m.end_date, m.resolved_at, m.payouts, o.token_id "
                         "FROM markets m JOIN outcomes o ON o.market_id = m.id AND o.idx = 0 "
                         "WHERE m.resolved = 1").fetchall()
        rows = []
        for mid, eid, cat, end, resolved_at, payouts, tok in mk:
            pay = json.loads(payouts or "[]")
            if sorted(pay) != ["0", "1"] or not end or not resolved_at:
                continue
            end_ts, closed_ts = _ts(end), _ts(resolved_at)
            snaps = con.execute("SELECT ts_received, best_bid, best_ask, bid_depth, ask_depth FROM snapshots "
                                "WHERE token_id = ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
                                "ORDER BY ts_received", (tok,)).fetchall()
            if len(snaps) < 5:
                continue
            series = [(_ts(s[0]), (s[1] + s[2]) / 2) for s in snaps]
            next_t = series[0][0] + 3 * 3600
            for (t, _), s in zip(series, snaps):
                if t < next_t or t >= min(end_ts, closed_ts):
                    continue
                next_t = t + step_s
                f, b = core_features(series, t, end_ts, cat), book_features(s[1], s[2], s[3], s[4])
                if f and b:
                    rows.append({"market_id": mid, "event_id": eid or "", "t_obs": t, "closed_ts": closed_ts,
                                 "category": cat, "y": int(pay[0] == "1"), "source": "live", **f, **b})
        return pd.DataFrame(rows)
    finally:
        con.close()
