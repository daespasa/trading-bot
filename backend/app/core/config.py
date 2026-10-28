"""Runtime settings. Everything comes from environment variables (see .env.example).

Paper trading only: there is deliberately no setting for keys, wallets or real orders.
"""
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default  # empty values (e.g. from compose) mean "use the default"


PROFILES = {
    # markets: initial monitored markets; max_markets: hard ceiling the UI can raise to.
    "low": dict(markets=20, max_markets=50, snapshot_seconds=60, retention_days=7, ml_threads=2),
    "normal": dict(markets=50, max_markets=200, snapshot_seconds=20, retention_days=30, ml_threads=4),
}


@dataclass
class Settings:
    resource_profile: str = field(default_factory=lambda: _env("RESOURCE_PROFILE", "low"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "../data")))
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", "../models")))
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    binance_url: str = field(default_factory=lambda: _env("BINANCE_URL", "https://api.binance.com"))
    api_token: str = field(default_factory=lambda: _env("API_TOKEN", ""))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"))
    collector_enabled: bool = field(default_factory=lambda: _env("COLLECTOR_ENABLED", "1") == "1")

    # paper trading defaults (spec section 8)
    initial_capital: Decimal = Decimal("100")
    max_trade_usdc: Decimal = Decimal("5")
    max_open_positions: int = 5
    max_daily_loss: Decimal = Decimal("10")

    scan_seconds: int = 5
    stale_seconds: int = 180  # book older than this is not tradable
    ws_down_halt_seconds: int = 30  # feed down longer than this halts new trades
    min_free_mem_mb_for_training: int = 1200

    def __post_init__(self):
        p = PROFILES.get(self.resource_profile, PROFILES["low"])
        self.markets = int(_env("MAX_MARKETS", str(p["markets"])))
        self.max_markets = p["max_markets"]
        self.markets = min(self.markets, self.max_markets)
        self.snapshot_seconds = int(_env("SNAPSHOT_SECONDS", str(p["snapshot_seconds"])))
        self.retention_days = int(_env("RETENTION_DAYS", str(p["retention_days"])))
        self.ml_threads = int(_env("ML_THREADS", str(p["ml_threads"])))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lab.db"


settings = Settings()

# Hard guarantee required by the spec. Nothing reads a key and nothing signs orders.
REAL_TRADING_ENABLED = False
