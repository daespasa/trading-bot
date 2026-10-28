"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { AreaSeries, Histogram, MultiLine, SignedBars } from "@/components/charts/charts"
import { PageHeader } from "@/components/dashboard/stat"
import { StatusBadge, Verdict, type Evidence } from "@/components/dashboard/verdict"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, post } from "@/lib/api"
import { dt, pct, signed, tone, usd } from "@/lib/format"
import type { WalletSummary } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

type WA = {
  wallet: WalletSummary; curve: { ts: string; equity: number }[]; drawdown: { ts: string; drawdown: number }[]
  daily_pnl: { day: string; pnl: number }[]; by_strategy: { strategy: string; pnl: number; trades: number }[]
  trade_pnls: number[]; costs: { ts: string; cumulative_costs: number }[]
  slippage_sensitivity: Record<string, { pnl: number; orders: number }>
}
type A = { wallets: WA[]; opportunity_counts: Record<string, string | number>[]; rejections: { key: string; count: number }[] }

function Performance() {
  const { data } = useQuery({ queryKey: ["analytics"], queryFn: () => api<A>("/analytics?days=60"), refetchInterval: 60_000 })
  const [sel, setSel] = useState("0")
  if (!data) return null
  const w = data.wallets[Number(sel)] ?? data.wallets[0]
  const series = Object.fromEntries(data.wallets.map((x) => [x.wallet.name, x.curve.map((p) => ({ ts: p.ts, value: p.equity - x.wallet.initial_capital }))]))
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle className="text-base">{t("Wallet comparison")}</CardTitle><CardDescription>{t("Net PnL in USDC over the same period and the same market data.")}</CardDescription></CardHeader>
        <CardContent>
          <MultiLine series={series} height={300} baseline={0} />
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm num">
              <thead><tr className="text-xs text-muted-foreground">{["Wallet", "Net PnL", "ROI", "Realised", "Fees", "Closed trades", "Win rate", "Max DD", "Evidence"].map((h) => <th key={h} className="px-2 py-1 text-left font-normal">{t(h)}</th>)}</tr></thead>
              <tbody>
                {data.wallets.map((x) => (
                  <tr key={x.wallet.id} className="border-t">
                    <td className="px-2 py-1.5">{x.wallet.name}</td>
                    <td className={`px-2 ${tone(x.wallet.net_pnl)}`}>{signed(x.wallet.net_pnl)}</td>
                    <td className="px-2">{pct(x.wallet.roi, 2)}</td>
                    <td className="px-2">{signed(x.wallet.realized_pnl)}</td>
                    <td className="px-2">{usd(x.wallet.fees, 3)}</td>
                    <td className="px-2">{x.wallet.closed_trades}</td>
                    <td className="px-2">{x.wallet.win_rate == null ? "—" : pct(x.wallet.win_rate).replace("+", "")}</td>
                    <td className="px-2">{usd(x.wallet.max_drawdown)}</td>
                    <td className="px-2"><Verdict e={x.wallet.evidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center gap-2">
        <Label>{t("Detail for")}</Label>
        <Select value={sel} onValueChange={setSel}>
          <SelectTrigger className="h-8 w-60"><SelectValue /></SelectTrigger>
          <SelectContent>{data.wallets.map((x, i) => <SelectItem key={x.wallet.id} value={String(i)}>{x.wallet.name}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {w && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card><CardHeader><CardTitle className="text-base">{t("Drawdown")}</CardTitle></CardHeader><CardContent><AreaSeries data={w.drawdown} x="ts" y="drawdown" label={t("Drawdown")} color="var(--loss)" /></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-base">{t("Daily PnL")}</CardTitle></CardHeader><CardContent><SignedBars data={w.daily_pnl} x="day" y="pnl" label="PnL" /></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-base">{t("PnL by strategy")}</CardTitle></CardHeader><CardContent><SignedBars data={w.by_strategy} x="strategy" y="pnl" label="PnL" /></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-base">{t("Distribution of trade results")}</CardTitle></CardHeader><CardContent><Histogram values={w.trade_pnls} /></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-base">{t("Cumulative costs")}</CardTitle><CardDescription>{t("Fees plus adverse slippage.")}</CardDescription></CardHeader><CardContent><AreaSeries data={w.costs} x="ts" y="cumulative_costs" label={t("Costs")} color="var(--chart-3)" /></CardContent></Card>
          <Card>
            <CardHeader><CardTitle className="text-base">{t("Sensitivity to execution assumptions")}</CardTitle><CardDescription>{t("Closed positions re-priced on their recorded books under each scenario. Orders a scenario could not fully fill are left out.")}</CardDescription></CardHeader>
            <CardContent>
              <table className="w-full text-sm num">
                <tbody>
                  {Object.entries(w.slippage_sensitivity).map(([k, v]) => (
                    <tr key={k} className="border-b last:border-0"><td className="py-1.5">{tCode(k).toLowerCase()}</td><td className={`text-right ${tone(v.pnl)}`}>{signed(v.pnl, 4)} USDC</td><td className="text-right text-muted-foreground">{t("{n} orders", { n: v.orders })}</td></tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">{t("Opportunities per strategy")}</CardTitle><CardDescription>{t("Strategies see different numbers of chances; compare results with that in mind.")}</CardDescription></CardHeader>
          <CardContent>
            <table className="w-full text-sm num">
              <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">{t("Strategy")}</th><th className="text-right font-normal">{t("Detected")}</th><th className="text-right font-normal">{t("Executed")}</th><th className="text-right font-normal">{t("Discarded")}</th></tr></thead>
              <tbody>{data.opportunity_counts.map((r) => (
                <tr key={String(r.strategy)} className="border-b last:border-0"><td className="py-1.5">{r.strategy}</td><td className="text-right">{r.detected ?? 0}</td><td className="text-right">{r.executed ?? 0}</td><td className="text-right">{r.rejected ?? 0}</td></tr>
              ))}</tbody>
            </table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">{t("Why trades were blocked")}</CardTitle><CardDescription>{t("Risk and sizing rejections since the last restart.")}</CardDescription></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {data.rejections.length === 0 && <p className="text-muted-foreground">{t("No rejections recorded.")}</p>}
            {data.rejections.slice(0, 15).map((r) => <div key={r.key} className="flex justify-between gap-3"><span className="truncate">{r.key.replace("|", ": ")}</span><span className="num">{r.count}</span></div>)}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

type Report = { id: number; kind: string; period_start: string; period_end: string; data: { wallets: { wallet: string; net_pnl: number; return: number | null; orders: number; evidence_all_time: Evidence }[]; best: string; worst: string } }

function Reports() {
  const { data = [] } = useQuery({ queryKey: ["reports"], queryFn: () => api<Report[]>("/reports") })
  const [md, setMd] = useState<string | null>(null)
  const preview = useMutation({ mutationFn: (kind: string) => post<{ markdown: string }>(`/reports/preview?kind=${kind}`), onSuccess: (r) => setMd(r.markdown) })
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => preview.mutate("daily")}>{t("Preview today")}</Button>
        <Button size="sm" variant="outline" onClick={() => preview.mutate("weekly")}>{t("Preview this week")}</Button>
      </div>
      {md && <Card><CardContent><pre className="overflow-x-auto text-xs whitespace-pre-wrap">{md}</pre></CardContent></Card>}
      {data.length === 0 && <p className="text-sm text-muted-foreground">{t("Daily reports are written after each UTC day ends, weekly reports after each week ends.")}</p>}
      {data.map((r) => (
        <Card key={r.id}>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">{t(r.kind === "daily" ? "Daily report, {a} to {b}" : "Weekly report, {a} to {b}", { a: dt(r.period_start), b: dt(r.period_end) })}</CardTitle>
            <a className="text-xs text-primary hover:underline" href={`/api/reports/${r.id}.md`} target="_blank" rel="noreferrer">Markdown</a>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {r.data.wallets.map((w) => (
              <div key={w.wallet} className="flex flex-wrap items-center justify-between gap-2 num">
                <span>{w.wallet}</span>
                <span className="flex items-center gap-3"><span className={tone(w.net_pnl)}>{signed(w.net_pnl)}</span><span>{t("{n} orders", { n: w.orders })}</span><Verdict e={w.evidence_all_time} /></span>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

type BT = { id: number; created_at: string; status: string; params: { start: string; end: string; scenario: string }; results: { error?: string; markets?: number; resolved_in_window?: number; assumptions?: string; strategies?: Record<string, { signals: number; orders: number; closed_trades: number; realized_pnl: number; unrealized_pnl: number; fees: number; max_drawdown: number; evidence: Evidence }> } }

function Backtests() {
  const qc = useQueryClient()
  const iso = (d: Date) => d.toISOString().slice(0, 16)
  const [start, setStart] = useState(() => iso(new Date(Date.now() - 86400e3)))
  const [end, setEnd] = useState(() => iso(new Date()))
  const [scenario, setScenario] = useState("REALISTIC")
  const { data = [] } = useQuery({ queryKey: ["backtests"], queryFn: () => api<BT[]>("/backtests"), refetchInterval: (q) => (q.state.data?.some((b) => b.status === "running") ? 3000 : false) })
  const run = useMutation({
    mutationFn: () => post("/backtests", { start: new Date(start).toISOString(), end: new Date(end).toISOString(), scenario }),
    onSuccess: () => { toast(t("Backtest started")); qc.invalidateQueries({ queryKey: ["backtests"] }) },
    onError: (e) => toast.error(String(e)),
  })
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("Replay recorded data")}</CardTitle>
          <CardDescription>{t("Replays the snapshots this lab recorded, with top-of-book liquidity only and fills on the next snapshot. Results are stored separately and never touch the live wallets.")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1"><Label htmlFor="bs">{t("From")}</Label><Input id="bs" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} className="h-8" /></div>
          <div className="grid gap-1"><Label htmlFor="be">{t("To")}</Label><Input id="be" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} className="h-8" /></div>
          <div className="grid gap-1"><Label>{t("Scenario")}</Label>
            <Select value={scenario} onValueChange={setScenario}><SelectTrigger className="h-8 w-40"><SelectValue /></SelectTrigger>
              <SelectContent>{["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"].map((s) => <SelectItem key={s} value={s}>{tCode(s).toLowerCase()}</SelectItem>)}</SelectContent></Select>
          </div>
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>{t("Run backtest")}</Button>
        </CardContent>
      </Card>
      {data.map((b) => (
        <Card key={b.id}>
          <CardHeader className="gap-1">
            <div className="flex items-center justify-between"><CardTitle className="text-base">Backtest #{b.id}, {tCode(b.params.scenario).toLowerCase()}</CardTitle><StatusBadge status={b.status} /></div>
            <CardDescription>{t("{a} to {b}", { a: dt(b.params.start), b: dt(b.params.end) })}{b.results.markets != null && `, ${t("{m} markets, {r} resolved in window", { m: b.results.markets, r: b.results.resolved_in_window ?? 0 })}`}</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            {b.results.error && <p className="text-sm text-loss">{b.results.error}</p>}
            {b.results.strategies && (
              <table className="w-full text-sm num">
                <thead><tr className="text-xs text-muted-foreground">{["Strategy", "Signals", "Orders", "Closed", "Realised", "Unrealised", "Fees", "Max DD", "Evidence"].map((h) => <th key={h} className="px-2 text-left font-normal">{t(h)}</th>)}</tr></thead>
                <tbody>{Object.entries(b.results.strategies).map(([k, r]) => (
                  <tr key={k} className="border-t"><td className="px-2 py-1.5">{k}</td><td className="px-2">{r.signals}</td><td className="px-2">{r.orders}</td><td className="px-2">{r.closed_trades}</td>
                    <td className={`px-2 ${tone(r.realized_pnl)}`}>{signed(r.realized_pnl, 3)}</td><td className="px-2">{signed(r.unrealized_pnl, 3)}</td><td className="px-2">{usd(r.fees, 3)}</td><td className="px-2">{usd(r.max_drawdown)}</td><td className="px-2"><Verdict e={r.evidence} /></td></tr>
                ))}</tbody>
              </table>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export default function AnalyticsPage() {
  return (
    <>
      <PageHeader title={t("Analytics")} description={t("Performance, costs and statistical evidence. A week of gains is not proof: look at the trade count and the confidence interval.")} />
      <Tabs defaultValue="performance">
        <TabsList className="mb-3" data-tour="analytics-tabs">
          <TabsTrigger value="performance">{t("Performance")}</TabsTrigger>
          <TabsTrigger value="reports">{t("Reports")}</TabsTrigger>
          <TabsTrigger value="backtests">{t("Backtests")}</TabsTrigger>
        </TabsList>
        <TabsContent value="performance"><Performance /></TabsContent>
        <TabsContent value="reports"><Reports /></TabsContent>
        <TabsContent value="backtests"><Backtests /></TabsContent>
      </Tabs>
    </>
  )
}
