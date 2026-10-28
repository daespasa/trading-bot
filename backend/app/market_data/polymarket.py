"""Read-only Polymarket client: Gamma (discovery, rules, resolution), CLOB REST (books, fees, history).
No authentication, no order endpoints. Rate limits (docs, Oct 2026): Gamma /markets 300/10s,
/events 500/10s, CLOB /book 1500/10s, /books 500/10s, /prices-history 1000/10s. We stay far below.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.core.config import settings
from app.paper_trading.execution import fee_rate_for

log = logging.getLogger(__name__)

CATEGORIES = ("crypto", "sports", "finance", "politics", "economics", "culture", "weather", "mentions", "tech",
              "geopolitics")


class PolymarketClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(timeout=20, headers={"User-Agent": "polymarket-paper-lab/1.0"})

    async def _get(self, url: str, params=None, retries: int = 4):
        for attempt in range(retries):
            try:
                r = await self.http.get(url, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as e:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
                log.debug("retry %s %s", url, e)

    async def events(self, **params) -> list[dict]:
        return await self._get(f"{settings.gamma_url}/events", params) or []

    async def markets(self, **params) -> list[dict]:
        return await self._get(f"{settings.gamma_url}/markets", params) or []

    async def markets_by_ids(self, ids: list[str], **params) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            out += await self.markets(id=ids[i:i + 50], limit=50, **params)
        return out

    async def books(self, token_ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(token_ids), 100):
            chunk = [{"token_id": t} for t in token_ids[i:i + 100]]
            r = await self.http.post(f"{settings.clob_url}/books", json=chunk)
            r.raise_for_status()
            out += r.json()
        return out

    async def fee_base_bps(self, token_id: str) -> int:
        try:
            return int((await self._get(f"{settings.clob_url}/fee-rate", {"token_id": token_id}, 2)).get("base_fee", 0))
        except Exception:
            return 0

    async def prices_history(self, token_id: str, start_ts: int, end_ts: int, fidelity: int = 60) -> list[tuple[int, float]]:
        """[(epoch_s, price)] ascending. `fidelity` is the resolution in minutes."""
        data = await self._get(f"{settings.clob_url}/prices-history",
                               {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity})
        return [(int(h["t"]), float(h["p"])) for h in (data or {}).get("history", [])]


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T")).astimezone(timezone.utc)
    except ValueError:
        return None


def _jl(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


def category_of(m: dict, event: dict | None = None) -> str:
    ft = (m.get("feeType") or "").lower()
    for c in CATEGORIES:
        if ft.startswith(c):
            return c
    for t in (event or {}).get("tags") or []:
        if (t.get("slug") or "") in CATEGORIES:
            return t["slug"]
    return "other"


def parse_market(m: dict, event: dict | None = None) -> tuple[dict, list[dict]] | None:
    """Gamma market -> (Market column dict, [Outcome dicts]); None if it has no order book."""
    tokens, names = _jl(m.get("clobTokenIds")), _jl(m.get("outcomes"))
    if not m.get("enableOrderBook") or not tokens or len(tokens) != len(names):
        return None
    ev = event or ((m.get("events") or [None])[0]) or {}
    cat = category_of(m, ev)
    fees_enabled = bool(m.get("feesEnabled"))
    closed = bool(m.get("closed"))
    resolved = closed and m.get("umaResolutionStatus") == "resolved"
    payouts = _jl(m.get("outcomePrices")) if resolved else None
    row = dict(
        id=str(m["id"]), condition_id=m.get("conditionId") or "", question=m.get("question") or "",
        slug=m.get("slug") or "", event_id=str(ev["id"]) if ev.get("id") else None, event_title=ev.get("title"),
        group_title=m.get("groupItemTitle"), category=cat, neg_risk=bool(m.get("negRisk")),
        neg_risk_augmented=bool(ev.get("negRiskAugmented")), end_date=_ts(m.get("endDate")),
        event_start=_ts(m.get("eventStartTime")), description=m.get("description") or "",
        resolution_source=m.get("resolutionSource") or "", active=bool(m.get("active")), closed=closed,
        accepting_orders=bool(m.get("acceptingOrders")), fees_enabled=fees_enabled, fee_type=m.get("feeType"),
        fee_rate=fee_rate_for(cat, fees_enabled), tick_size=Decimal(str(m.get("orderPriceMinTickSize") or "0.01")),
        min_size=Decimal(str(m.get("orderMinSize") or "5")), volume_24h=float(m.get("volume24hr") or 0),
        liquidity=float(m.get("liquidityNum") or 0), resolved=resolved, payouts=payouts,
        resolved_at=_ts(m.get("closedTime")) if resolved else None, updated_at=datetime.now(timezone.utc))
    outs = [dict(token_id=t, market_id=row["id"], idx=i, name=n) for i, (t, n) in enumerate(zip(tokens, names))]
    return row, outs


def event_exhaustive(event: dict, selected_ids: set[str]) -> bool:
    """negRisk event whose open markets are all selected and whose closed markets all resolved NO."""
    if not event.get("negRisk") or event.get("negRiskAugmented"):
        return False
    for m in event.get("markets") or []:
        if m.get("closed"):
            prices = _jl(m.get("outcomePrices"))
            if not prices or float(prices[0]) != 0.0:
                return False
        elif str(m["id"]) not in selected_ids:
            return False
    return True
