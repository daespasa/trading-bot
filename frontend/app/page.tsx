"use client"
import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { MultiLine } from "@/components/charts/charts"
import { PlainSummary } from "@/components/dashboard/plain-summary"
import { PageHeader, Stat } from "@/components/dashboard/stat"
import { StatusBadge, Verdict } from "@/components/dashboard/verdict"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { ago, pct, signed, tone, usd } from "@/lib/format"
import type { Health, Opportunity, Order, WalletSummary } from "@/types/api"
import { t } from "@/lib/i18n"

type Overview = {
  totals: { virtual_balance: number; total_equity: number; initial_capital: number; net_pnl: number; roi: number
    win_rate: number | null; max_drawdown: number; open_positions: number; closed_trades: number }
  wallets: WalletSummary[]
  curves: Record<string, { ts: string; equity: number }[]>
  active_strategies: number
  ml_experimental: boolean
  markets_monitored: number
  health: Health
  recent_orders: Order[]
  recent_opportunities: Opportunity[]
}

export default function OverviewPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/overview"), refetchInterval: 30_000 })
  const { data: ml } = useQuery({ queryKey: ["ml", "models"], queryFn: () => api<{ models: { kind: string; feature_set: string; status: string }[] }>("/ml/models?limit=40") })
  if (error) return <p className="text-sm text-loss">{t("Cannot reach the backend:")} {String(error)}</p>
  if (isLoading || !data) return <Skeleton className="h-96 w-full" />
  const tot = data.totals
  const series = Object.fromEntries(Object.entries(data.curves).map(([k, v]) => [k, v.map((p) => ({ ts: p.ts, value: p.equity }))]))
  return (
    <>
      <PageHeader
        title={t("Is there an edge?")}
        description={t("Five virtual wallets trade the same live Polymarket data with fictitious USDC. Results only count once the statistics say so.")}
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" data-tour="stats">
        <Stat label={t("Virtual balance")} value={usd(tot.virtual_balance)} sub={t("of {v} USDC", { v: usd(tot.initial_capital, 0) })} />
        <Stat label={t("Total equity")} value={usd(tot.total_equity)} sub={t("cash + positions at best bid")} />
        <Stat label={t("Net PnL")} value={signed(tot.net_pnl)} className={tot.net_pnl < 0 ? "text-loss" : ""} sub={`ROI ${pct(tot.roi, 2)}`} />
        <Stat label={t("Win rate")} value={tot.win_rate == null ? "—" : pct(tot.win_rate).replace("+", "")} sub={t("{n} closed trades across wallets (may repeat)", { n: tot.closed_trades })} />
        <Stat label={t("Max drawdown")} value={usd(tot.max_drawdown)} sub={t("worst wallet, USDC")} className={tot.max_drawdown > 0 ? "text-loss" : ""} />
        <Stat label={t("Active strategies")} value={data.active_strategies} />
        <Stat label={t("Open positions")} value={tot.open_positions} />
        <Stat label={t("Markets monitored")} value={data.markets_monitored} />
        <Stat label={t("Feed")} value={data.health.feed_healthy ? t("Live") : t("Down")} className={data.health.feed_healthy ? "text-gain" : "text-loss"} sub={t("{n} reconnects", { n: data.health.reconnects })} />
        <Stat label={t("Uptime")} value={`${(data.health.uptime_s / 3600).toFixed(1)} h`} sub={t("profile {p}", { p: data.health.profile })} />
      </div>

      <Card className="mt-4" data-tour="equity">
        <CardHeader>
          <CardTitle className="text-base">{t("Equity by wallet")}</CardTitle>
        </CardHeader>
        <CardContent>
          <MultiLine series={series} height={320} />
          <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3" data-tour="verdicts">
            {data.wallets.map((w) => (
              <div key={w.id} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{w.name}</div>
                  <div className={`text-xs num ${w.net_pnl < 0 ? "text-loss" : "text-muted-foreground"}`}>
                    {signed(w.net_pnl)} USDC ({pct(w.roi, 2)})
                  </div>
                </div>
                <Verdict e={w.evidence} />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">{t("Latest simulated orders")}</CardTitle>
            <Link href="/trades" className="text-xs text-primary hover:underline">{t("All trades")}</Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recent_orders.length === 0 && <p className="text-sm text-muted-foreground">{t("No orders yet. Strategies trade only when an opportunity passes risk checks.")}</p>}
            {data.recent_orders.map((o) => (
              <div key={o.id} className="flex items-center justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <div className="truncate">{o.title || o.market_id}</div>
                  <div className="text-xs text-muted-foreground">{o.strategy}, {ago(o.created_at)}</div>
                </div>
                <div className="text-right num">
                  <div>{o.side} {o.filled_shares.toFixed(2)} @ {o.avg_price?.toFixed(3)}</div>
                  <StatusBadge status={o.status} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">{t("Latest opportunities")}</CardTitle>
            <Link href="/opportunities" className="text-xs text-primary hover:underline">{t("All opportunities")}</Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recent_opportunities.length === 0 && <p className="text-sm text-muted-foreground">{t("Nothing detected yet.")}</p>}
            {data.recent_opportunities.map((o) => (
              <div key={o.id} className="flex items-center justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <div className="truncate">{o.title}</div>
                  <div className="truncate text-xs text-muted-foreground">{o.strategy}{o.reason ? `: ${o.reason}` : ""}</div>
                </div>
                <div className="text-right num">
                  <div className={tone(o.net_edge)}>{pct(o.net_edge)}</div>
                  <StatusBadge status={o.status} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
      <div className="mt-4">
        <PlainSummary data={{ ...data, models: ml?.models }} />
      </div>
    </>
  )
}
