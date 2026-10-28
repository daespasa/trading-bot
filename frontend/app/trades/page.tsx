"use client"
import { useQuery } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { useMemo, useState } from "react"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/dashboard/stat"
import { StatusBadge } from "@/components/dashboard/verdict"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, token } from "@/lib/api"
import { dt, signed, tone } from "@/lib/format"
import type { Order, WalletSummary } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

export default function TradesPage() {
  const [wallet, setWallet] = useState("all")
  const { data: wallets = [] } = useQuery({ queryKey: ["wallets"], queryFn: () => api<WalletSummary[]>("/wallets") })
  const qs = wallet === "all" ? "" : `wallet_id=${wallet}`
  const { data } = useQuery({ queryKey: ["trades", wallet], queryFn: () => api<{ items: Order[] }>(`/trades?limit=5000&${qs}`) })
  const rows = useMemo(() => {
    const names: Record<number, string> = Object.fromEntries(wallets.map((w) => [w.id, w.name]))
    return (data?.items ?? []).map((o) => ({ ...o, wallet_name: names[o.wallet_id] ?? String(o.wallet_id) }))
  }, [data, wallets])
  const columns = useMemo<Col<Order & { wallet_name: string }>[]>(() => {
  return [
    { id: "date", header: "Date", accessorFn: (o) => o.created_at, cell: (c) => dt(c.getValue()) },
    { id: "wallet", header: "Wallet", accessorFn: (o) => o.wallet_name },
    { id: "market", header: "Market", accessorFn: (o) => o.title, cell: (c) => <span className="block max-w-[22rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
    { id: "strategy", header: "Strategy", accessorFn: (o) => o.strategy },
    { id: "side", header: "Side", accessorFn: (o) => `${tCode(o.side)} ${o.outcome}` },
    { id: "entry", header: "Entry", accessorFn: (o) => o.avg_price, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toFixed(4)) },
    { id: "exit", header: "Exit", accessorFn: (o) => o.exit_price, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toFixed(4)) },
    { id: "shares", header: "Shares", accessorFn: (o) => o.filled_shares, cell: (c) => Number(c.getValue()).toFixed(2) },
    { id: "amount", header: "Amount", accessorFn: (o) => o.notional, cell: (c) => Number(c.getValue()).toFixed(2) },
    { id: "fee", header: "Fees", accessorFn: (o) => o.fee, cell: (c) => Number(c.getValue()).toFixed(4) },
    { id: "slip", header: "Slippage/sh", accessorFn: (o) => o.slippage, cell: (c) => Number(c.getValue()).toFixed(4) },
    { id: "pnl", header: "Profit", accessorFn: (o) => o.realized_pnl, cell: (c) => <span className={tone(c.getValue())}>{signed(c.getValue(), 4)}</span> },
    { id: "status", header: "Status", accessorFn: (o) => (o.position_status ? `${tCode(o.status)}, ${tCode(o.position_status)}` : o.status), cell: (c) => <StatusBadge status={c.getValue()} /> },
    { id: "unc", header: "Uncertainty", accessorFn: (o) => o.uncertainty ?? o.reason ?? "", cell: (c) => <span className="block max-w-[16rem] truncate text-muted-foreground" title={c.getValue()}>{c.getValue()}</span> },
  ]
  }, [])
  const csv = `/api/trades.csv?${qs}${token() ? `&token=${encodeURIComponent(token())}` : ""}`
  return (
    <>
      <PageHeader
        title={t("Trade history")}
        description={t("Each row is one simulated order leg. Profit is the realised result of the position it opened, after fees, once it is closed or settled.")}
        actions={
          <Button asChild variant="outline" size="sm">
            <a href={csv}><Download /> {t("Export CSV")}</a>
          </Button>
        }
      />
      <DataTable
        data={rows}
        columns={columns}
        empty={t("No simulated orders yet.")}
        toolbar={
          <Select value={wallet} onValueChange={setWallet}>
            <SelectTrigger className="h-8 w-56" aria-label={t("Wallet")}><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("All wallets")}</SelectItem>
              {wallets.map((w) => <SelectItem key={w.id} value={String(w.id)}>{w.name}</SelectItem>)}
            </SelectContent>
          </Select>
        }
      />
    </>
  )
}
