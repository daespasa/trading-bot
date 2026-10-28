"use client"
import { useQuery } from "@tanstack/react-query"
import { CircleCheck, CircleX, Search } from "lucide-react"
import { useMemo, useState } from "react"
import { AreaSeries, MultiLine } from "@/components/charts/charts"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader, Stat } from "@/components/dashboard/stat"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api } from "@/lib/api"
import { dt, pct, signed, usd } from "@/lib/format"
import { t, tCode } from "@/lib/i18n"
import { cn } from "@/lib/utils"

type Row = {
  wallet_id: number; wallet: string; strategy: string; status: string; status_reasons: string[]; model_status: string | null
  trades: number; open_trades: number; clusters: number; days: number; gross_pnl: number; net_pnl: number; fees: number
  realized_pnl: number; unrealized_pnl_bid: number; open_cost: number; roi_on_deployed: number | null; roi_on_capital: number | null
  annualized: number | null; annualized_note: string | null; win_rate: number | null; profit_factor: number | null
  expectancy: number | null; avg_win: number | null; avg_loss: number | null; max_drawdown: number; max_drawdown_pct: number
  max_exposure: number; avg_capital_used: number; avg_duration_min: number | null
  stats: { clusters: number; mean?: number; ci95?: [number, number]; t?: number | null }
  halves: number[] | null; positive_days: number; days_with_trades: number
  scenarios: Record<string, number>; scenario_incomplete_trades: Record<string, number>
  cost_sensitivity: { fees_x0: number; fees_x1: number; fees_x2: number; breakeven_fee_multiple: number | null }
  market_baseline_ev: number | null; excess_over_market: number | null; contradicted_share: number | null
  checks: Record<string, number>; execution_ratio: number | null; decisions: Record<string, number>
  rejection_reasons: Record<string, number>
  curve: { ts: string; equity: number; pnl: number; drawdown: number }[]; fee_curve: { ts: string; fees: number }[]
  exposure: { ts: string; exposure: number }[]
}
type Acc = {
  wallet_id: number; wallet: string; initial_capital: number; available_balance: number; reserved_capital: number
  position_cost_basis: number; open_value_bid: number; realized_pnl: number; unrealized_pnl: number; total_equity: number
  net_return: number; fees: number; open_positions: number; checks_passed: boolean; problems: string[]
}
type Data = {
  strategies: Row[]; accounting: Acc[]; gates: Record<string, number>; funnel: Record<string, Record<string, number>>
  equity_curves: Record<string, { ts: string; equity: number }[]>
  scenarios: Record<string, { latency_ms: number; depth_fraction: number; extra_ticks: number; fee_mode: string; edge_margin: number }>
}
type Research = { items: { id: string; name: string; score: number | null; capital_min: string; complexity: number; costs: string; liquidity: number; risk: number; access: number; spain_access: string; regulatory: string; frequency: string; hardware: number; data: number; testability: number; automation: number; verdict: string; next_step: string }[] }

const QUALITY: Record<string, { label: string; cls: string }> = {
  RESEARCH: { label: "No data yet", cls: "border-border text-muted-foreground" },
  BACKTESTING: { label: "Backtest only", cls: "border-border text-muted-foreground" },
  "INSUFFICIENT DATA": { label: "Insufficient data, provisional result", cls: "border-chart-3/50 text-chart-3" },
  "PAPER TESTING": { label: "Inconclusive", cls: "border-chart-3/50 text-chart-3" },
  PROMISING: { label: "Statistically promising", cls: "border-primary/60 text-primary" },
  REJECTED: { label: "Discarded", cls: "border-loss/50 text-loss" },
  "READY FOR HUMAN REVIEW": { label: "Ready for human review", cls: "border-primary bg-primary/10 text-primary" },
}

function Quality({ status }: { status: string }) {
  const q = QUALITY[status] ?? QUALITY.RESEARCH
  return <span className={cn("inline-flex rounded-full border px-2 py-0.5 text-xs whitespace-nowrap", q.cls)}>{t(q.label)}</span>
}

const GATE_TEXT: Record<string, string> = {
  RESEARCH: "Enabled but no paper trades yet.",
  BACKTESTING: "Only backtest results exist; no live paper trades yet.",
  "INSUFFICIENT DATA": "Has traded, but fewer than {min_clusters} independent trades or {min_days} days. Nothing can be concluded yet.",
  "PAPER TESTING": "Enough data, but not every condition is met: CI above zero, conservative execution positive, both halves positive, fills not contradicted by real trades.",
  REJECTED: "Losing with statistical support (CI entirely below zero), or losing under both realistic and conservative execution.",
  PROMISING: "All conditions above are met. Keeps running in paper.",
  "READY FOR HUMAN REVIEW": "Promising, plus {ready_min_clusters}+ independent trades over {ready_min_days}+ days, drawdown under {ready_max_dd_pct} of capital, positive in {ready_min_positive_weeks} of the last 4 weeks and beating the market baseline. A human decides; real trading stays off.",
}
const STAGES: Record<string, string> = {
  opportunity_detected: "Opportunity detected", opportunity_executable: "Opportunity executable",
  paper_order_submitted: "Paper order submitted", paper_order_filled: "Paper order filled",
  position_opened: "Position opened", position_closed: "Position closed", market_settled: "Market settled",
}

// Gains are not shown in green unless the strategy has statistical support.
const ptone = (v: number | null | undefined, status?: string) =>
  v == null || v === 0 ? "" : v < 0 ? "text-loss" : status === "PROMISING" || status === "READY FOR HUMAN REVIEW" ? "text-gain" : ""
const n2 = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d))

const columns: Col<Row>[] = [
  { id: "wallet", header: "Wallet", accessorFn: (r) => r.wallet },
  { id: "strategy", header: "Strategy", accessorFn: (r) => r.strategy },
  { id: "status", header: "Validation", accessorFn: (r) => r.status, cell: (c) => <Quality status={c.getValue()} /> },
  { id: "net", header: "Net PnL", accessorFn: (r) => r.net_pnl, cell: (c) => <span className={ptone(c.getValue(), c.row.original.status)}>{signed(c.getValue())}</span> },
  { id: "unreal", header: "Unrealised", accessorFn: (r) => r.unrealized_pnl_bid, cell: (c) => signed(c.getValue()) },
  { id: "roi", header: "ROI on deployed", accessorFn: (r) => r.roi_on_deployed, cell: (c) => pct(c.getValue(), 1) },
  { id: "trades", header: "Trades", accessorFn: (r) => r.trades },
  { id: "clusters", header: "Independent", accessorFn: (r) => r.clusters },
  { id: "win", header: "Win rate", accessorFn: (r) => r.win_rate, cell: (c) => (c.getValue() == null ? "—" : `${(c.getValue() * 100).toFixed(0)}%`) },
  { id: "pf", header: "Profit factor", accessorFn: (r) => r.profit_factor, cell: (c) => n2(c.getValue()) },
  { id: "exp", header: "Expectancy", accessorFn: (r) => r.expectancy, cell: (c) => n2(c.getValue(), 3) },
  { id: "ci", header: "95% CI per trade", accessorFn: (r) => r.stats.ci95?.[0] ?? null, cell: (c) => { const ci = c.row.original.stats.ci95; return ci ? `[${ci[0].toFixed(2)}, ${ci[1].toFixed(2)}]` : "—" } },
  { id: "dd", header: "Max DD", accessorFn: (r) => r.max_drawdown, cell: (c) => usd(c.getValue()) },
  { id: "fees", header: "Fees", accessorFn: (r) => r.fees, cell: (c) => usd(c.getValue(), 3) },
  { id: "cons", header: "Conservative PnL", accessorFn: (r) => r.scenarios.CONSERVATIVE, cell: (c) => <span className={ptone(c.getValue(), c.row.original.status)}>{signed(c.getValue())}</span> },
  { id: "excess", header: "vs market baseline", accessorFn: (r) => r.excess_over_market, cell: (c) => signed(c.getValue()) },
  { id: "days", header: "Days", accessorFn: (r) => r.days, cell: (c) => Number(c.getValue()).toFixed(1) },
]

function Trace({ orderId, onClose }: { orderId: number | null; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["trace", orderId], enabled: orderId != null,
    queryFn: () => api<{ question: string; stages: { stage: string; at: string | null; info: Record<string, unknown> }[] }>(`/orders/${orderId}/trace`),
  })
  return (
    <Dialog open={orderId != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("Order trace")} #{orderId}</DialogTitle>
          <DialogDescription>{data?.question}</DialogDescription>
        </DialogHeader>
        <ol className="space-y-3">
          {data?.stages.map((s, i) => (
            <li key={s.stage} className="rounded-md border p-3">
              <div className="flex items-center justify-between gap-2 text-sm font-medium">
                <span>{i + 1}. {t(STAGES[s.stage] ?? s.stage)}</span>
                <span className="text-xs text-muted-foreground num">{s.at ? dt(s.at) : t("not reached")}</span>
              </div>
              <pre className="mt-2 max-h-56 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">{JSON.stringify(s.info, null, 1)}</pre>
            </li>
          ))}
        </ol>
      </DialogContent>
    </Dialog>
  )
}

type TradeRec = { group_id: string; question: string; opened_at: string; closed: boolean; realized: number; cost: number; fees: number; market_ev: number | null; cluster: string; order_ids: number[]; checks: Record<string, number>; scenarios: Record<string, { pnl: number; complete: boolean }> }

function Detail({ r }: { r: Row }) {
  const [trace, setTrace] = useState<number | null>(null)
  const { data: trades = [] } = useQuery({
    queryKey: ["profitability", "trades", r.wallet_id, r.strategy],
    queryFn: () => api<TradeRec[]>(`/profitability/trades?wallet_id=${r.wallet_id}&strategy=${r.strategy}`),
  })
  const tcols = useMemo<Col<TradeRec>[]>(() => [
    { id: "o", header: "Opened", accessorFn: (x) => x.opened_at, cell: (c) => dt(c.getValue()) },
    { id: "q", header: "Market", accessorFn: (x) => x.question, cell: (c) => <span className="block max-w-[22rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
    { id: "st", header: "Status", accessorFn: (x) => (x.closed ? tCode("closed") : tCode("open")) },
    { id: "c", header: "Cost", accessorFn: (x) => x.cost, cell: (c) => usd(c.getValue(), 3) },
    { id: "p", header: "Realised", accessorFn: (x) => x.realized, cell: (c) => signed(c.getValue(), 3) },
    { id: "ev", header: "Market-implied", accessorFn: (x) => x.market_ev, cell: (c) => signed(c.getValue(), 3) },
    { id: "cons", header: "Conservative", accessorFn: (x) => x.scenarios.CONSERVATIVE?.pnl, cell: (c) => signed(c.getValue(), 3) },
    { id: "chk", header: "Checks", accessorFn: (x) => Object.entries(x.checks).map(([k, v]) => `${t(k)}${v > 1 ? ` ×${v}` : ""}`).join(", ") || t("none"), cell: (c) => <span className="block max-w-[14rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
    { id: "tr", header: "Trace", enableSorting: false, accessorFn: (x) => x.order_ids[0], cell: (c) => (
      <Button size="sm" variant="ghost" onClick={() => setTrace(c.getValue())} aria-label={t("Open trace")}><Search /></Button>
    ) },
  ], [])
  const st = r.stats
  return (
    <Card className="mt-4">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">{r.strategy} · {r.wallet}</CardTitle>
          <Quality status={r.status} />
          {r.model_status && <Badge variant="outline">{tCode(r.model_status)}</Badge>}
        </div>
        <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">{r.status_reasons.map((x) => <li key={x}>{x}</li>)}</ul>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          <Stat label={t("Gross PnL")} value={signed(r.gross_pnl)} sub={t("before fees")} />
          <Stat label={t("Net PnL")} value={signed(r.net_pnl)} className={ptone(r.net_pnl, r.status)} sub={t("realised, after fees")} />
          <Stat label={t("Unrealised")} value={signed(r.unrealized_pnl_bid)} sub={t("{n} open, valued at best bid", { n: r.open_trades })} />
          <Stat label={t("Fees")} value={usd(r.fees, 3)} sub={r.cost_sensitivity.breakeven_fee_multiple ? t("break-even at ×{x} fees", { x: r.cost_sensitivity.breakeven_fee_multiple.toFixed(1) }) : undefined} />
          <Stat label={t("Independent trades")} value={`${st.clusters} / ${r.trades}`} sub={t("clusters / trades")} />
          <Stat label={t("95% CI per trade")} value={st.ci95 ? `${st.ci95[0].toFixed(2)} … ${st.ci95[1].toFixed(2)}` : "—"} sub={t("cluster bootstrap")} />
          <Stat label={t("Win rate")} value={r.win_rate == null ? "—" : `${(r.win_rate * 100).toFixed(0)}%`} sub={t("avg win {w} / avg loss {l}", { w: n2(r.avg_win), l: n2(r.avg_loss) })} />
          <Stat label={t("Profit factor")} value={n2(r.profit_factor)} sub={t("expectancy {e}", { e: n2(r.expectancy, 3) })} />
          <Stat label={t("Max drawdown")} value={usd(r.max_drawdown)} sub={pct(r.max_drawdown_pct, 1).replace("+", "")} />
          <Stat label={t("Max exposure")} value={usd(r.max_exposure)} sub={t("avg capital used {v}", { v: usd(r.avg_capital_used) })} />
          <Stat label={t("Execution ratio")} value={r.execution_ratio == null ? "—" : `${(r.execution_ratio * 100).toFixed(0)}%`} sub={t("filled / submitted")} />
          <Stat label={t("Annualised")} value={r.annualized == null ? "—" : pct(r.annualized, 1)} sub={r.annualized == null ? t("not meaningful yet") : undefined} />
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          <div>
            <div className="mb-1 text-sm font-medium">{t("Realised equity")}</div>
            <AreaSeries data={r.curve} x="ts" y="equity" label={t("Equity")} height={180} />
          </div>
          <div>
            <div className="mb-1 text-sm font-medium">{t("Drawdown")}</div>
            <AreaSeries data={r.curve} x="ts" y="drawdown" label={t("Drawdown")} color="var(--loss)" height={180} />
          </div>
          <div>
            <div className="mb-1 text-sm font-medium">{t("Cumulative fees")}</div>
            <AreaSeries data={r.fee_curve} x="ts" y="fees" label={t("Fees")} color="var(--chart-3)" height={180} />
          </div>
          <div>
            <div className="mb-1 text-sm font-medium">{t("Exposure")}</div>
            <AreaSeries data={r.exposure} x="ts" y="exposure" label={t("Exposure")} color="var(--chart-4)" height={180} />
          </div>
        </div>
        {Object.keys(r.rejection_reasons).length > 0 && (
          <div className="text-sm">
            <div className="mb-1 font-medium">{t("Why opportunities were not taken")}</div>
            {Object.entries(r.rejection_reasons).map(([k, v]) => <div key={k} className="flex justify-between gap-3 text-muted-foreground"><span className="truncate">{k}</span><span className="num">{v}</span></div>)}
          </div>
        )}
        <div>
          <div className="mb-1 text-sm font-medium">{t("Trades")}</div>
          <DataTable data={trades} columns={tcols} height={360} empty={t("No trades yet.")} />
        </div>
        <Trace orderId={trace} onClose={() => setTrace(null)} />
      </CardContent>
    </Card>
  )
}

function Verdict({ rows }: { rows: Row[] }) {
  const good = rows.filter((r) => r.status === "PROMISING" || r.status === "READY FOR HUMAN REVIEW")
  const bad = rows.filter((r) => r.status === "REJECTED")
  const text = good.length
    ? t("{n} strategy/wallet pairs are statistically promising. This is still paper money and needs more time before any human review.", { n: good.length })
    : t("No strategy has demonstrated profitability yet. Positive numbers below are provisional until enough independent trades accumulate.")
  return (
    <Card className="mb-4">
      <CardContent className="flex items-start gap-3 text-sm">
        {good.length ? <CircleCheck className="mt-0.5 size-5 shrink-0 text-primary" /> : <CircleX className="mt-0.5 size-5 shrink-0 text-chart-3" />}
        <div>
          <p className="font-medium">{text}</p>
          {bad.length > 0 && <p className="mt-1 text-muted-foreground">{t("Discarded: {names}.", { names: bad.map((r) => `${r.strategy} (${r.wallet})`).join(", ") })}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export default function ProfitabilityPage() {
  const { data, isLoading } = useQuery({ queryKey: ["profitability"], queryFn: () => api<Data>("/profitability"), refetchInterval: 60_000 })
  const { data: research } = useQuery({ queryKey: ["research"], queryFn: () => api<Research>("/research/opportunities") })
  const [sel, setSel] = useState<string | null>(null)
  if (isLoading || !data) return <Skeleton className="h-96 w-full" />
  const acc = data.accounting
  const sum = (k: keyof Acc) => acc.reduce((s, a) => s + Number(a[k]), 0)
  const init = sum("initial_capital")
  const equity = sum("total_equity")
  const rows = [...data.strategies].sort((a, b) => b.net_pnl - a.net_pnl)
  const selected = rows.find((r) => `${r.wallet_id}|${r.strategy}` === sel) ?? rows[0]
  const bad = acc.filter((a) => !a.checks_passed)
  const series = Object.fromEntries(Object.entries(data.equity_curves).map(([k, v]) => [k, v.map((p) => ({ ts: p.ts, value: p.equity }))]))
  return (
    <>
      <PageHeader
        title={t("Profitability Center")}
        description={t("Are we really making (paper) money, or is the simulator flattering us? Results per strategy, under three execution models, with independent checks against the exchange.")}
      />
      <Verdict rows={rows} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t("Initial capital")} value={usd(init)} sub={t("{n} independent virtual accounts", { n: acc.length })} />
        <Stat label={t("Current equity")} value={usd(equity)} sub={t("net return {r}", { r: pct((equity - init) / init, 2) })} />
        <Stat label={t("Realised PnL")} value={signed(sum("realized_pnl"))} sub={t("closed and settled only")} />
        <Stat label={t("Unrealised PnL")} value={signed(sum("unrealized_pnl"))} sub={t("{n} open positions at best bid", { n: sum("open_positions") })} />
        <Stat label={t("Fees paid")} value={usd(sum("fees"), 3)} />
        <Stat label={t("Capital in positions")} value={usd(sum("position_cost_basis"))} sub={t("cost basis of open positions")} />
        <Stat label={t("Max drawdown")} value={usd(Math.max(0, ...rows.map((r) => r.max_drawdown)))} sub={t("worst strategy, realised")} />
        <Stat
          label={t("Accounting")}
          value={bad.length ? t("{n} problems", { n: bad.length }) : t("Reconciled")}
          className={bad.length ? "text-loss" : ""}
          sub={t("two independent routes must match")}
        />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{t("Totals add up independent wallets. Combined can repeat trades of other wallets, so judge each strategy in the table below, not by the totals.")}</p>
      {bad.map((a) => <p key={a.wallet_id} className="mt-1 text-sm text-loss">{a.wallet}: {a.problems.join("; ")}</p>)}

      <Tabs defaultValue="strategies" className="mt-6">
        <TabsList>
          <TabsTrigger value="strategies">{t("Strategies")}</TabsTrigger>
          <TabsTrigger value="execution">{t("Execution models")}</TabsTrigger>
          <TabsTrigger value="evolution">{t("Evolution")}</TabsTrigger>
          <TabsTrigger value="gates">{t("Validation gates")}</TabsTrigger>
          <TabsTrigger value="research">{t("Next markets")}</TabsTrigger>
        </TabsList>

        <TabsContent value="strategies">
          <p className="mb-2 text-xs text-muted-foreground">{t("Click a row to open its report. 'Independent' counts trades after grouping those that share an event or a crypto time window.")}</p>
          <DataTable
            data={rows}
            columns={columns}
            height={420}
            empty={t("No strategy has traded yet.")}
            onRowClick={(r) => setSel(`${r.wallet_id}|${r.strategy}`)}
          />
          {selected && <Detail key={`${selected.wallet_id}|${selected.strategy}`} r={selected} />}
        </TabsContent>

        <TabsContent value="execution">
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("Same trades, three execution models")}</CardTitle>
                <CardDescription>{t("Each entry re-priced on the order books recorded at the time. Optimistic is only an upper bound.")}</CardDescription>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-sm num">
                  <thead><tr className="text-xs text-muted-foreground">{["Strategy", "Wallet", "OPTIMISTIC", "REALISTIC", "CONSERVATIVE"].map((h) => <th key={h} className="px-2 py-1 text-left font-normal">{tCode(t(h))}</th>)}</tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={`${r.wallet_id}${r.strategy}`} className="border-t">
                        <td className="px-2 py-1.5">{r.strategy}</td><td className="px-2">{r.wallet}</td>
                        {["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"].map((k) => (
                          <td key={k} className={cn("px-2", ptone(r.scenarios[k], r.status))}>
                            {signed(r.scenarios[k])}{r.scenario_incomplete_trades[k] ? <span className="text-xs text-chart-3"> ({t("{n} partial", { n: r.scenario_incomplete_trades[k] })})</span> : null}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("Checks against the real exchange")}</CardTitle>
                <CardDescription>{t("Exchange book: our book compared with the exchange's own book at fill time. Real trades: did anyone actually buy at our price within 30 s?")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                {rows.map((r) => {
                  const c = r.checks
                  const keys = Object.keys(c)
                  return (
                    <div key={`${r.wallet_id}${r.strategy}`}>
                      <div className="font-medium">{r.strategy} · {r.wallet}</div>
                      <div className="text-muted-foreground">{keys.length ? keys.map((k) => `${t(k)}: ${c[k]}`).join(" · ") : t("no checks yet (orders before the validator existed, or pending)")}</div>
                      {r.contradicted_share != null && <div className="text-xs text-muted-foreground">{t("contradicted by real trades: {p}", { p: `${(r.contradicted_share * 100).toFixed(0)}%` })}</div>}
                    </div>
                  )
                })}
              </CardContent>
            </Card>
            <Card className="xl:col-span-2">
              <CardHeader><CardTitle className="text-base">{t("Execution model parameters")}</CardTitle><CardDescription>{t("Editable in Settings.")}</CardDescription></CardHeader>
              <CardContent>
                <table className="w-full text-sm num">
                  <thead><tr className="text-xs text-muted-foreground">{["Scenario", "Latency", "Depth taken", "Adverse ticks", "Fees", "Safety margin"].map((h) => <th key={h} className="px-2 text-left font-normal">{t(h)}</th>)}</tr></thead>
                  <tbody>{Object.entries(data.scenarios).map(([k, v]) => (
                    <tr key={k} className="border-t"><td className="px-2 py-1.5">{tCode(k).toLowerCase()}</td><td className="px-2">{v.latency_ms} ms</td><td className="px-2">{(v.depth_fraction * 100).toFixed(0)}%</td><td className="px-2">{v.extra_ticks}</td><td className="px-2">{t(v.fee_mode)}</td><td className="px-2">{v.edge_margin}</td></tr>
                  ))}</tbody>
                </table>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="evolution" className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-base">{t("Equity by wallet")}</CardTitle></CardHeader>
            <CardContent><MultiLine series={series} height={300} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">{t("From opportunity to settlement")}</CardTitle><CardDescription>{t("How many opportunities survive each step. Most should not.")}</CardDescription></CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-sm num">
                <thead><tr className="text-xs text-muted-foreground">{["Strategy", "detected", "executable", "submitted", "filled", "opened", "closed", "settled", "rejected_by_risk"].map((h) => <th key={h} className="px-2 text-left font-normal">{t(h)}</th>)}</tr></thead>
                <tbody>{Object.entries(data.funnel).map(([k, f]) => (
                  <tr key={k} className="border-t"><td className="px-2 py-1.5">{k}</td>{["detected", "executable", "submitted", "filled", "opened", "closed", "settled", "rejected_by_risk"].map((x) => <td key={x} className="px-2">{f[x] ?? 0}</td>)}</tr>
                ))}</tbody>
              </table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="gates">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("How a strategy moves forward")}</CardTitle>
              <CardDescription>{t("Minimum sizes are necessary, never sufficient: the decision rests on confidence intervals over independent trades and on conservative execution. Ready for human review never enables real trading.")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {["RESEARCH", "BACKTESTING", "INSUFFICIENT DATA", "PAPER TESTING", "REJECTED", "PROMISING", "READY FOR HUMAN REVIEW"].map((s) => (
                <div key={s} className="flex gap-3"><span className="w-56 shrink-0"><Quality status={s} /></span><span className="text-muted-foreground">{t(GATE_TEXT[s], data.gates)}</span></div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="research">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("Where else to look for an edge")}</CardTitle>
              <CardDescription>{t("Qualitative ranking for a home setup and a resident in Spain. Not evidence of profit: every line must pass the same gates. Regulatory notes are pointers to verify.")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {research?.items.map((o, i) => (
                <div key={o.id} className="rounded-md border p-3 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div className="font-medium">{o.score != null ? `${i + 1}. ` : ""}{o.name}</div>
                    <div className="text-xs text-muted-foreground num">{o.score != null ? t("score {s} / 5", { s: o.score.toFixed(2) }) : t("benchmark")}</div>
                  </div>
                  <p className="mt-1 max-w-[90ch] text-muted-foreground">{o.verdict}</p>
                  <div className="mt-2 grid gap-x-6 gap-y-1 text-xs text-muted-foreground md:grid-cols-2">
                    <div><span className="text-foreground">{t("Minimum capital")}:</span> {o.capital_min}</div>
                    <div><span className="text-foreground">{t("Costs")}:</span> {o.costs}</div>
                    <div><span className="text-foreground">{t("From Spain")}:</span> {o.spain_access}</div>
                    <div><span className="text-foreground">{t("Regulation and tax")}:</span> {o.regulatory}</div>
                    <div><span className="text-foreground">{t("Frequency")}:</span> {o.frequency}</div>
                    <div className="num"><span className="text-foreground">{t("Criteria")}:</span> {t("complexity")} {o.complexity} · {t("liquidity")} {o.liquidity} · {t("risk")} {o.risk} · {t("data")} {o.data} · {t("testability")} {o.testability} · {t("automation")} {o.automation}</div>
                  </div>
                  <p className="mt-2 text-xs"><span className="text-foreground">{t("Next step")}:</span> <span className="text-muted-foreground">{o.next_step}</span></p>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </>
  )
}
