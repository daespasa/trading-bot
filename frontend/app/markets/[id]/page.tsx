"use client"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "next/navigation"
import { MultiLine } from "@/components/charts/charts"
import { PageHeader, Stat } from "@/components/dashboard/stat"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { dt } from "@/lib/format"
import { t, tCode } from "@/lib/i18n"

type Detail = {
  id: string; question: string; description: string; resolution_source: string; event_title: string | null; category: string
  end_date: string | null; resolved: boolean; payouts: string[] | null; neg_risk: boolean; fee_rate: number; tick_size: number
  min_size: number; volume_24h: number; liquidity: number; slug: string; outcomes: string[]
  books: Record<string, { bids: [string, string][]; asks: [string, string][] }>
  history: Record<string, { ts: string; bid: number | null; ask: number | null }[]>
}

export default function MarketDetail() {
  const { id } = useParams<{ id: string }>()
  const { data } = useQuery({ queryKey: ["markets", id], queryFn: () => api<Detail>(`/markets/${id}`), refetchInterval: 5000 })
  if (!data) return <Skeleton className="h-96 w-full" />
  const series = Object.fromEntries(
    Object.entries(data.history).map(([name, pts]) => [
      t("{name} mid", { name }),
      pts.filter((p) => p.bid != null && p.ask != null).map((p) => ({ ts: p.ts, value: ((p.bid ?? 0) + (p.ask ?? 0)) / 2 })),
    ]),
  )
  return (
    <>
      <PageHeader title={data.question} description={data.event_title ?? undefined} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <Stat label={t("Category")} value={tCode(data.category)} />
        <Stat label={t("Ends")} value={dt(data.end_date)} />
        <Stat label={t("Taker fee rate")} value={data.fee_rate ? data.fee_rate.toFixed(2) : t("none")} sub="fee = C·rate·p·(1−p)" />
        <Stat label={t("Tick / min size")} value={`${data.tick_size} / ${data.min_size}`} />
        <Stat label={t("Liquidity")} value={data.liquidity.toLocaleString(undefined, { maximumFractionDigits: 0 })} />
        <Stat label={t("Status")} value={data.resolved ? `${t("Resolved")} ${JSON.stringify(data.payouts)}` : t("Not resolved")} />
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Mid price, last 24 h")}</CardTitle></CardHeader>
        <CardContent><MultiLine series={series} height={260} /></CardContent>
      </Card>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {Object.entries(data.books).map(([name, b]) => (
          <Card key={name}>
            <CardHeader><CardTitle className="text-base">{t("Order book:")} {name}</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm num">
              <div>
                <div className="mb-1 text-xs text-muted-foreground">{t("Bids (price, shares)")}</div>
                {b.bids.map(([p, s]) => (
                  <div key={p} className="flex justify-between text-gain"><span>{p}</span><span>{Number(s).toFixed(0)}</span></div>
                ))}
              </div>
              <div>
                <div className="mb-1 text-xs text-muted-foreground">{t("Asks (price, shares)")}</div>
                {b.asks.map(([p, s]) => (
                  <div key={p} className="flex justify-between text-loss"><span>{p}</span><span>{Number(s).toFixed(0)}</span></div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Resolution rules")}</CardTitle></CardHeader>
        <CardContent>
          <p className="max-w-[80ch] text-sm whitespace-pre-line text-muted-foreground">{data.description || t("No description published.")}</p>
          <a className="mt-3 inline-block text-sm text-primary hover:underline" href={`https://polymarket.com/market/${data.slug}`} target="_blank" rel="noreferrer">
            {t("Open on Polymarket")}
          </a>
        </CardContent>
      </Card>
    </>
  )
}
