import type { Evidence } from "@/components/dashboard/verdict"

export type WalletSummary = {
  id: number; name: string; status: string; scenario: string; strategies: string[]; experiment: string
  initial_capital: number; cash: number; committed: number; positions_value: number; equity: number
  net_pnl: number; roi: number; realized_pnl: number; unrealized_pnl: number; fees: number
  open_positions: number; closed_trades: number; win_rate: number | null; max_drawdown: number; max_drawdown_pct: number
  max_trade_usdc: number; max_open_positions: number; max_daily_loss: number; created_at: string; evidence: Evidence
}

export type Order = {
  id: number; created_at: string; wallet_id: number; strategy: string; market_id: string; title: string; outcome: string
  side: string; requested_shares: number; filled_shares: number; avg_price: number | null; notional: number; fee: number
  slippage: number; status: string; uncertainty: string | null; reason: string | null; scenario: string; latency_ms: number
  group_id: string; position_status?: string | null; exit_price?: number | null; realized_pnl?: number | null
}

export type Opportunity = {
  id: number; strategy: string; title: string; market_id: string; kind: string; guaranteed: boolean
  gross_edge: number; costs: number; net_edge: number; liquidity: number; risk: string; status: string
  reason: string | null; first_seen: string; last_seen: string; seen_count: number
  details: Record<string, unknown>; legs: { token_id: string; outcome: string; limit: string; market_id: string }[] | null
}

export type OutcomeQuote = {
  token_id: string; name: string; bid: number | null; ask: number | null; spread: number | null
  bid_depth: number | null; ask_depth: number | null; age_s: number | null; last_trade: number | null
}

export type MarketRow = {
  id: string; question: string; event_title: string | null; category: string; end_date: string | null; neg_risk: boolean
  slug: string; fees_enabled: boolean; fee_rate: number; outcomes: OutcomeQuote[]; volume_24h: number; liquidity: number
}

export type Health = {
  ws_connected: boolean; feed_healthy: boolean; markets: number; uptime_s: number; trading_halted: boolean
  training_running: boolean; training_paused: boolean; profile: string; loop_errors: Record<string, string>
  reconnects: number; messages: number; crypto_fresh: boolean; seconds_since_message: number | null
}
