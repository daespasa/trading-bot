"use client"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/dashboard/stat"
import { StatusBadge } from "@/components/dashboard/verdict"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { ago, pct, tone } from "@/lib/format"
import type { Opportunity } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

type Decision = { ts: string; wallet: string; strategy: string; title: string; decision: string; detail: string }

const columns: Col<Opportunity>[] = [
  { id: "last", header: "Last seen", accessorFn: (o) => o.last_seen, cell: (c) => ago(c.getValue()) },
  { id: "title", header: "Market", accessorFn: (o) => o.title, cell: (c) => <span className="block max-w-[26rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
  { id: "strategy", header: "Strategy", accessorFn: (o) => o.strategy },
  { id: "kind", header: "Type", accessorFn: (o) => (o.guaranteed ? t("arbitrage (proven)") : tCode(o.kind)) },
  {
    id: "prices", header: "Prices", enableSorting: false,
    accessorFn: (o) => {
      const d = o.details as Record<string, unknown>
      if (Array.isArray(d.asks)) return `${t("asks")} ${(d.asks as string[]).join(" + ")}`
      if (d.p_fair != null) return t("fair {f} vs ask {a}", { f: Number(d.p_fair).toFixed(3), a: String(d.ask) })
      return ""
    },
  },
  { id: "gross", header: "Gross", accessorFn: (o) => o.gross_edge, cell: (c) => pct(c.getValue(), 2) },
  { id: "costs", header: "Costs", accessorFn: (o) => o.costs, cell: (c) => pct(c.getValue(), 2) },
  { id: "net", header: "Net", accessorFn: (o) => o.net_edge, cell: (c) => <span className={tone(c.getValue())}>{pct(c.getValue(), 2)}</span> },
  { id: "liq", header: "Liquidity $", accessorFn: (o) => o.liquidity, cell: (c) => Number(c.getValue()).toFixed(0) },
  { id: "risk", header: "Risk", accessorFn: (o) => tCode(o.risk) },
  { id: "status", header: "Status", accessorFn: (o) => o.status, cell: (c) => <StatusBadge status={c.getValue()} /> },
  { id: "reason", header: "Why", accessorFn: (o) => o.reason ?? "", cell: (c) => <span className="block max-w-[18rem] truncate text-muted-foreground" title={c.getValue()}>{c.getValue()}</span> },
  { id: "seen", header: "Seen", accessorFn: (o) => o.seen_count },
]

export default function OpportunitiesPage() {
  const [strategy, setStrategy] = useState("all")
  const [status, setStatus] = useState("all")
  const params = new URLSearchParams({ limit: "1000" })
  if (strategy !== "all") params.set("strategy", strategy)
  if (status !== "all") params.set("status", status)
  const { data } = useQuery({
    queryKey: ["opportunities", strategy, status],
    queryFn: () => api<{ total: number; items: Opportunity[]; decisions: Decision[] }>(`/opportunities?${params}`),
  })
  return (
    <>
      <PageHeader
        title={t("Opportunities")}
        description={t("Every candidate the strategies found, including the ones discarded after fees, depth or risk checks. Net edge is per USDC deployed at the planned size.")}
      />
      <DataTable
        data={data?.items ?? []}
        columns={columns}
        empty={t("No opportunities yet.")}
        toolbar={
          <>
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger className="h-8 w-44" aria-label={t("Strategy")}><SelectValue /></SelectTrigger>
              <SelectContent>
                {["all", "arb_yes_no", "arb_multi", "related_markets", "crypto_momentum", "mean_reversion", "ai_lightgbm", "ai_logistic"].map((s) => (
                  <SelectItem key={s} value={s}>{s === "all" ? t("All strategies") : s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="h-8 w-36" aria-label={t("Status")}><SelectValue /></SelectTrigger>
              <SelectContent>
                {["all", "detected", "executed", "rejected"].map((s) => (
                  <SelectItem key={s} value={s}>{s === "all" ? t("Any status") : tCode(s)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
      />
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Recent wallet decisions")}</CardTitle></CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {(data?.decisions ?? []).length === 0 && <p className="text-muted-foreground">{t("No decisions since the last restart.")}</p>}
          {data?.decisions.slice(0, 30).map((d, i) => (
            <div key={i} className="flex gap-3">
              <span className="w-16 shrink-0 text-xs text-muted-foreground num">{ago(d.ts)}</span>
              <StatusBadge status={d.decision} />
              <span className="min-w-0 truncate"><span className="text-muted-foreground">{d.wallet}:</span> {d.title}. {d.detail}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  )
}
