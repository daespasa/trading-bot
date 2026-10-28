from app.strategies.ai import LightGBMStrategy, LogisticStrategy
from app.strategies.arbitrage import MultiOutcomeArbitrage, RelatedMarkets, YesNoArbitrage
from app.strategies.rules import CryptoMomentum, MeanReversion

REGISTRY = {s.name: s for s in (YesNoArbitrage(), MultiOutcomeArbitrage(), RelatedMarkets(), CryptoMomentum(),
                                MeanReversion(), LightGBMStrategy(), LogisticStrategy())}
