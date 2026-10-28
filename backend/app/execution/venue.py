"""Design only: the boundary a future real-money integration would implement. Nothing here sends orders.

Real trading stays disabled (`REAL_TRADING_ENABLED = False`, asserted by the risk engine). A live venue may only
be written after the preconditions in docs/LIVE_TRADING_DESIGN.md are met and the owner authorises it in writing.
Paper execution already follows this shape (plan -> marketable limit order -> fills), so a live adapter would
replace only the fill source, not the strategy, risk or accounting code.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str  # idempotency key, identical to the paper engine's
    token_id: str
    side: str  # BUY | SELL
    shares: Decimal
    limit_price: Decimal  # marketable limit, fill-and-kill


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    filled_shares: Decimal
    avg_price: Decimal | None
    fee: Decimal
    venue_order_id: str | None
    raw: dict


class ExecutionVenue(Protocol):
    name: str
    is_real_money: bool

    async def submit(self, order: OrderRequest) -> OrderResult: ...
    async def cancel_all(self) -> None: ...  # used by the kill switch
    async def balances(self) -> dict[str, Decimal]: ...  # reconciled against the local ledger every cycle
