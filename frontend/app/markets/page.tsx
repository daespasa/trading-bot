"use client"
import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { useMemo, useState } from "react"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/dashboard/stat"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { dt } from "@/lib/format"
import type { MarketRow } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

const q = (v: number | null) => (v == null ? "—" : v.toFixed(3))

const columns: Col<MarketRow>[] = [
  {
    id: "question", header: "Market", accessorFn: (m) => m.question,
    cell: ({ row }) => (
      <Link href={`/markets/${row.original.id}`} className="block max-w-[28rem] truncate hover:text-primary" title={row.original.question}>
        {row.original.question}
      </Link>
    ),
  },
  { id: "category", header: "Category", accessorFn: (m) => tCode(m.category) },
  { id: "o0", header: "Outcome", accessorFn: (m) => m.outcomes[0]?.name },
  { id: "bid", header: "Bid", accessorFn: (m) => m.outcomes[0]?.bid, cell: (c) => q(c.getValue()) },
  { id: "ask", header: "Ask", accessorFn: (m) => m.outcomes[0]?.ask, cell: (c) => q(c.getValue()) },
  { id: "spread", header: "Spread", accessorFn: (m) => m.outcomes[0]?.spread, cell: (c) => q(c.getValue()) },
  { id: "sum", header: "Ask sum", accessorFn: (m) => (m.outcomes.every((o) => o.ask != null) ? m.outcomes.reduce((s, o) => s + (o.ask ?? 0), 0) : null), cell: (c) => q(c.getValue()) },
  { id: "depth", header: "Ask depth $", accessorFn: (m) => m.outcomes[0]?.ask_depth, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toFixed(0)) },
  { id: "liq", header: "Liquidity", accessorFn: (m) => m.liquidity, cell: (c) => Number(c.getValue()).toLocaleString(undefined, { maximumFractionDigits: 0 }) },
  { id: "vol", header: "Vol 24h", accessorFn: (m) => m.volume_24h, cell: (c) => Number(c.getValue()).toLocaleString(undefined, { maximumFractionDigits: 0 }) },
  { id: "fee", header: "Taker fee", accessorFn: (m) => m.fee_rate, cell: (c) => (Number(c.getValue()) ? Number(c.getValue()).toFixed(2) : t("none")) },
  { id: "age", header: "Book age", accessorFn: (m) => m.outcomes[0]?.age_s, cell: (c) => (c.getValue() == null ? "—" : `${Math.round(Number(c.getValue()))}s`) },
  { id: "end", header: "Ends", accessorFn: (m) => m.end_date, cell: (c) => dt(c.getValue()) },
]

export default function MarketsPage() {
  const [category, setCategory] = useState("all")
  const { data = [], isLoading } = useQuery({ queryKey: ["markets"], queryFn: () => api<MarketRow[]>("/markets"), refetchInterval: 5000 })
  const cats = useMemo(() => [...new Set(data.map((m) => m.category))].sort(), [data])
  const rows = useMemo(() => (category === "all" ? data : data.filter((m) => m.category === category)), [data, category])
  return (
    <>
      <PageHeader
        title={t("Live markets")}
        description={t("Markets streamed over the CLOB WebSocket. Prices are executable quotes: buys pay the ask, sells receive the bid. Refreshed every 5 seconds.")}
      />
      <DataTable
        data={rows}
        columns={columns}
        empty={isLoading ? t("Loading markets…") : t("No markets monitored yet. Discovery runs every minute.")}
        toolbar={
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="h-8 w-40" aria-label={t("Category")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("All categories")}</SelectItem>
              {cats.map((c) => (
                <SelectItem key={c} value={c}>{tCode(c)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
    </>
  )
}
