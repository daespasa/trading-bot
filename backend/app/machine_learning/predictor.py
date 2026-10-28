"""Live predictions with the newest model of each kind (core feature set). Features are computed from the
same CLOB price-history source used in training, so train/serve features match. Predictions are stored
for every model, active or not, so live out-of-sample accuracy accumulates either way."""
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

import joblib
import pandas as pd
from sqlalchemy import select

from app.database import session as db
from app.database.models import MLModel, Prediction
from app.machine_learning.features import core_features
from app.market_data.polymarket import PolymarketClient
from app.paper_trading.execution import taker_fee

log = logging.getLogger(__name__)


class Predictor:
    def __init__(self, client: PolymarketClient):
        self.client = client
        self._cache: dict[int, dict] = {}
        self.latest: dict[tuple[str, str], dict] = {}  # (kind, market_id) -> prediction for strategies

    async def _models(self) -> list[MLModel]:
        async with db.read() as s:
            rows = (await s.scalars(select(MLModel).where(MLModel.feature_set == "core", MLModel.path.is_not(None))
                                    .order_by(MLModel.id.desc()))).all()
        newest = {}
        for r in rows:
            newest.setdefault(r.kind, r)
        return list(newest.values())

    def _load(self, m: MLModel) -> dict:
        # joblib (pickle) is safe here: only files written by our own training job into MODELS_DIR are loaded.
        if m.id not in self._cache:
            self._cache[m.id] = joblib.load(m.path)
        return self._cache[m.id]

    async def run(self, state) -> int:
        models = await self._models()
        self._cache = {k: v for k, v in self._cache.items() if k in {m.id for m in models}}
        if not models:
            return 0
        now = time.time()
        rows = []
        for mv in list(state.markets.values()):
            if len(mv.tokens) != 2 or mv.closed or not mv.end_date or mv.end_date.timestamp() - now < 3600:
                continue
            try:
                series = await self.client.prices_history(mv.tokens[0], int(now - 8 * 86400), int(now), 60)
            except Exception as e:
                log.warning("history fetch failed for %s: %s", mv.id, e)
                continue
            f = core_features([(float(t), p) for t, p in series], now, mv.end_date.timestamp(), mv.category)
            if not f:
                continue
            book = state.books.get(mv.tokens[0])
            ask = book.best_ask if book else None
            for m in models:
                bundle = self._load(m)
                p = float(bundle["model"].predict_proba(pd.DataFrame([f])[bundle["features"]])[0, 1])
                edge = None
                if ask is not None:
                    edge = p - float(ask) - float(taker_fee(Decimal(1), ask, mv.fee_rate))
                rows.append(Prediction(model_id=m.id, model_kind=m.kind, market_id=mv.id, token_id=mv.tokens[0],
                                       question=mv.question, p_model=p, p_market=f["p"],
                                       ask=float(ask) if ask is not None else None, edge=edge))
                self.latest[(m.kind, mv.id)] = {"p": p, "p_market": f["p"], "model_id": m.id, "status": m.status,
                                                "ts": datetime.now(timezone.utc)}
        if rows:
            async with db.write() as s:
                s.add_all(rows)
        return len(rows)
