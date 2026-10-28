"""ORM models. Money is stored as Decimal-in-text (exact on SQLite); market-data floats are plain floats.

Swapping `Dec` to `Numeric(20, 8)` is the only change needed for PostgreSQL.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Dec(TypeDecorator):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo; normalise everything to aware UTC."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    question: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(200), default="")
    event_id: Mapped[str | None] = mapped_column(String(32), index=True)
    event_title: Mapped[str | None] = mapped_column(Text)
    group_title: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="other")
    neg_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    neg_risk_augmented: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when this event's open markets are all known and every closed sibling resolved NO, so the
    # open set is provably exhaustive (used by the multi-outcome arbitrage proof).
    event_exhaustive: Mapped[bool] = mapped_column(Boolean, default=False)
    end_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    event_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    description: Mapped[str] = mapped_column(Text, default="")
    resolution_source: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    fees_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    fee_type: Mapped[str | None] = mapped_column(String(60))
    fee_rate: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    fee_base_bps: Mapped[int] = mapped_column(Integer, default=0)
    tick_size: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0.01"))
    min_size: Mapped[Decimal] = mapped_column(Dec, default=Decimal("5"))
    volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    monitored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payouts: Mapped[list | None] = mapped_column(JSON(none_as_null=True))  # per outcome index, e.g. ["1","0"]
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Outcome(Base):
    __tablename__ = "outcomes"
    token_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))


class Snapshot(Base):
    """Top of book + depth per token, sampled every SNAPSHOT_SECONDS. High-frequency; subject to retention."""
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[str] = mapped_column(String(100))
    market_id: Mapped[str] = mapped_column(String(32))
    ts_source: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ts_received: Mapped[datetime] = mapped_column(UTCDateTime)
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    bid_size: Mapped[float | None] = mapped_column(Float)  # shares at best level
    ask_size: Mapped[float | None] = mapped_column(Float)
    bid_depth: Mapped[float | None] = mapped_column(Float)  # USDC within 5 ticks
    ask_depth: Mapped[float | None] = mapped_column(Float)
    last_trade: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(10))
    __table_args__ = (Index("ix_snap_token_ts", "token_id", "ts_received"), Index("ix_snap_ts", "ts_received"))


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    strategy: Mapped[str] = mapped_column(String(40), index=True)
    market_id: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))  # arbitrage | directional | speculative
    guaranteed: Mapped[bool] = mapped_column(Boolean, default=False)
    gross_edge: Mapped[float] = mapped_column(Float)  # per USDC deployed
    costs: Mapped[float] = mapped_column(Float)
    net_edge: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="detected")  # detected|executed|rejected
    reason: Mapped[str | None] = mapped_column(Text)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    strategies: Mapped[list] = mapped_column(JSON, default=list)
    scenario: Mapped[str] = mapped_column(String(20), default="REALISTIC")
    initial_capital: Mapped[Decimal] = mapped_column(Dec)
    cash: Mapped[Decimal] = mapped_column(Dec)
    max_trade_usdc: Mapped[Decimal] = mapped_column(Dec, default=Decimal("5"))
    max_open_positions: Mapped[int] = mapped_column(Integer, default=5)
    max_daily_loss: Mapped[Decimal] = mapped_column(Dec, default=Decimal("10"))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|paused|archived
    experiment: Mapped[str] = mapped_column(String(40), default="exp-1")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PaperOrder(Base):
    """One simulated order (a leg). `fills` holds the per-level fills (PaperFill)."""
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(200), unique=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    group_id: Mapped[str] = mapped_column(String(200), index=True)
    market_id: Mapped[str] = mapped_column(String(32))
    token_id: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(100), default="")
    side: Mapped[str] = mapped_column(String(4))
    requested_shares: Mapped[Decimal] = mapped_column(Dec)
    filled_shares: Mapped[Decimal] = mapped_column(Dec)
    avg_price: Mapped[Decimal | None] = mapped_column(Dec)
    notional: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    fee: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    slippage: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))  # vs top of book at signal time
    status: Mapped[str] = mapped_column(String(20))  # filled|partial|rejected
    uncertainty: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    scenario: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fills: Mapped[list] = mapped_column(JSON, default=list)
    book: Mapped[dict] = mapped_column(JSON, default=dict)
    signal: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    group_id: Mapped[str] = mapped_column(String(200), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    market_id: Mapped[str] = mapped_column(String(32), index=True)
    token_id: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(100), default="")
    shares: Mapped[Decimal] = mapped_column(Dec)
    cost: Mapped[Decimal] = mapped_column(Dec)  # cash paid incl. entry fees
    fees: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    entry_price: Mapped[Decimal] = mapped_column(Dec)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exit_price: Mapped[Decimal | None] = mapped_column(Dec)
    proceeds: Mapped[Decimal | None] = mapped_column(Dec)  # cash received incl. exit fees
    realized_pnl: Mapped[Decimal | None] = mapped_column(Dec)
    exit_reason: Mapped[str | None] = mapped_column(String(40))  # resolution|take_profit|stop_loss|time_stop
    exit_rules: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # {take_profit, stop_loss, max_hold_minutes}


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    cash: Mapped[Decimal] = mapped_column(Dec)
    positions_value: Mapped[Decimal] = mapped_column(Dec)
    equity: Mapped[Decimal] = mapped_column(Dec)
    __table_args__ = (Index("ix_eq_wallet_ts", "wallet_id", "ts"),)


class MLModel(Base):
    __tablename__ = "ml_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # logistic | lightgbm | xgboost
    version: Mapped[str] = mapped_column(String(40))
    feature_set: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    path: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    n_test: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40))  # ACTIVE | NO DEMONSTRATED PREDICTIVE EDGE | INSUFFICIENT TRAINING DATA
    reason: Mapped[str | None] = mapped_column(Text)
    training_run_id: Mapped[int | None] = mapped_column(Integer)


class TrainingRun(Base):
    __tablename__ = "training_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|done|failed|skipped
    message: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[dict] = mapped_column(JSON, default=dict)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("ml_models.id"), index=True)
    model_kind: Mapped[str] = mapped_column(String(20))
    market_id: Mapped[str] = mapped_column(String(32), index=True)
    token_id: Mapped[str] = mapped_column(String(100))
    question: Mapped[str] = mapped_column(Text, default="")
    p_model: Mapped[float] = mapped_column(Float)
    p_market: Mapped[float] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float)  # p_model - ask - fee
    outcome: Mapped[float | None] = mapped_column(Float)  # filled at resolution (payout of YES token)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # daily | weekly
    period_start: Mapped[datetime] = mapped_column(UTCDateTime)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    markdown: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("ux_report_period", "kind", "period_start", unique=True),)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="running")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)


class Decision(Base):
    """What each wallet decided about each opportunity (persisted so execution ratios survive restarts).
    Repeated identical decisions only bump `count`/`last_ts`."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, index=True)
    strategy: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(20))  # executed | rejected | no_fill
    reason: Mapped[str] = mapped_column(String(300), default="")
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (Index("ux_decision", "wallet_id", "opportunity_id", "decision", "reason", unique=True),)


class ExecutionCheck(Base):
    """Independent evidence about a simulated fill, kept apart from the order so history is never rewritten.
    kind: rest_book (exchange book fetched at fill time) | prints (real trades around the fill time)."""
    __tablename__ = "execution_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(20))  # match|ws_better|ws_worse|corroborated|contradicted|unverifiable
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (Index("ux_check", "order_id", "kind", unique=True),)
