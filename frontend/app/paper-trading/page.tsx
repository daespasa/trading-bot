"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Pause, Play, Plus, RotateCcw } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/dashboard/stat"
import { StatusBadge, Verdict } from "@/components/dashboard/verdict"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, post } from "@/lib/api"
import { dt, pct, signed, tone, usd } from "@/lib/format"
import type { WalletSummary } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

const STRATEGIES = ["arb_yes_no", "arb_multi", "related_markets", "crypto_momentum", "mean_reversion", "ai_lightgbm", "ai_logistic"]

type Pos = {
  id: number; question: string; outcome: string; strategy: string; shares: number; cost: number; entry_price: number
  mark: number | null; value: number | null; fees: number; opened_at: string; closed_at: string | null
  exit_price: number | null; realized_pnl: number | null; exit_reason: string | null
}

const posColumns: Col<Pos>[] = [
  { id: "opened", header: "Opened", accessorFn: (p) => p.opened_at, cell: (c) => dt(c.getValue()) },
  { id: "q", header: "Market", accessorFn: (p) => p.question, cell: (c) => <span className="block max-w-[24rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
  { id: "o", header: "Outcome", accessorFn: (p) => p.outcome },
  { id: "s", header: "Strategy", accessorFn: (p) => p.strategy },
  { id: "sh", header: "Shares", accessorFn: (p) => p.shares, cell: (c) => Number(c.getValue()).toFixed(2) },
  { id: "e", header: "Entry", accessorFn: (p) => p.entry_price, cell: (c) => Number(c.getValue()).toFixed(4) },
  { id: "c", header: "Cost", accessorFn: (p) => p.cost, cell: (c) => usd(c.getValue(), 4) },
  { id: "m", header: "Bid / exit", accessorFn: (p) => p.exit_price ?? p.mark, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toFixed(4)) },
  {
    id: "pnl", header: "PnL", accessorFn: (p) => p.realized_pnl ?? (p.value != null ? p.value - p.cost : null),
    cell: (c) => <span className={tone(c.getValue())}>{signed(c.getValue(), 4)}</span>,
  },
  { id: "r", header: "Closed by", accessorFn: (p) => (p.exit_reason ? tCode(p.exit_reason) : "") },
]

function CreateWallet() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(() => t("My wallet"))
  const [capital, setCapital] = useState("100")
  const [scenario, setScenario] = useState("REALISTIC")
  const [strats, setStrats] = useState<string[]>(["arb_yes_no"])
  const m = useMutation({
    mutationFn: () => post("/wallets", { name, capital, scenario, strategies: strats }),
    onSuccess: () => {
      toast.success(t("Wallet created"))
      qc.invalidateQueries({ queryKey: ["wallets"] })
      setOpen(false)
    },
    onError: (e) => toast.error(String(e)),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm"><Plus /> {t("Create wallet")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("Create a virtual wallet")}</DialogTitle>
          <DialogDescription>{t("It starts trading the selected strategies on the next scan, with fictitious USDC.")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5"><Label htmlFor="wn">{t("Name")}</Label><Input id="wn" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="grid gap-1.5"><Label htmlFor="wc">{t("Initial capital (USDC)")}</Label><Input id="wc" type="number" min={1} value={capital} onChange={(e) => setCapital(e.target.value)} /></div>
          <div className="grid gap-1.5">
            <Label>{t("Execution scenario")}</Label>
            <Select value={scenario} onValueChange={setScenario}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"].map((s) => <SelectItem key={s} value={s}>{tCode(s).toLowerCase()}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label>{t("Strategies")}</Label>
            {STRATEGIES.map((s) => (
              <label key={s} className="flex items-center justify-between text-sm">
                {s}
                <Switch checked={strats.includes(s)} onCheckedChange={(v) => setStrats(v ? [...strats, s] : strats.filter((x) => x !== s))} />
              </label>
            ))}
          </div>
        </div>
        <DialogFooter>
          <Button onClick={() => m.mutate()} disabled={m.isPending || !strats.length}>{t("Create wallet")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResetExperiment() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [capital, setCapital] = useState("100")
  const m = useMutation({
    mutationFn: () => post<{ experiment: string }>("/experiments/reset", { capital }),
    onSuccess: (r) => {
      toast.success(t("Started {e}", { e: r.experiment }))
      qc.invalidateQueries()
      setOpen(false)
    },
    onError: (e) => toast.error(String(e)),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline"><RotateCcw /> {t("Restart experiment")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("Restart the experiment?")}</DialogTitle>
          <DialogDescription>
            {t("Current wallets are archived with their full history and five fresh default wallets start at the capital below. Archived results stay exportable.")}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5"><Label htmlFor="rc">{t("Capital per wallet (USDC)")}</Label><Input id="rc" type="number" min={1} value={capital} onChange={(e) => setCapital(e.target.value)} /></div>
        <DialogFooter>
          <Button variant="destructive" onClick={() => m.mutate()} disabled={m.isPending}>{t("Archive and restart")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function PaperTradingPage() {
  const qc = useQueryClient()
  const { data: wallets = [] } = useQuery({ queryKey: ["wallets"], queryFn: () => api<WalletSummary[]>("/wallets"), refetchInterval: 15_000 })
  const [sel, setSel] = useState<number | null>(null)
  const [tab, setTab] = useState("open")
  const current = sel ?? wallets[0]?.id ?? null
  const { data: positions = [] } = useQuery({
    queryKey: ["positions", current, tab],
    queryFn: () => api<Pos[]>(`/wallets/${current}/positions?status=${tab}`),
    enabled: current != null,
  })
  const toggle = useMutation({
    mutationFn: (w: WalletSummary) => post(`/wallets/${w.id}/${w.status === "running" ? "pause" : "resume"}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wallets"] }),
  })
  const rows = useMemo(() => positions, [positions])
  return (
    <>
      <PageHeader
        title={t("Paper trading")}
        description={t("Virtual wallets with independent books. Default limits: 5 USDC per trade, 5 open positions, 10 USDC daily loss.")}
        actions={<><CreateWallet /><ResetExperiment /></>}
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-tour="wallets">
        {wallets.map((w) => (
          <Card key={w.id} className={`cursor-pointer gap-3 py-4 ${current === w.id ? "ring-2 ring-primary" : ""}`} onClick={() => setSel(w.id)}>
            <CardContent className="space-y-2 px-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{w.name}</div>
                  <div className="text-xs text-muted-foreground">{t("{s} execution", { s: tCode(w.scenario).toLowerCase() })}, {w.experiment}</div>
                </div>
                <Button
                  size="icon" variant="ghost" aria-label={w.status === "running" ? t("Pause wallet") : t("Resume wallet")}
                  onClick={(e) => { e.stopPropagation(); toggle.mutate(w) }}
                >
                  {w.status === "running" ? <Pause /> : <Play />}
                </Button>
              </div>
              <div className="flex items-baseline gap-3 num">
                <span className="text-2xl font-semibold">{usd(w.equity)}</span>
                <span className={`text-sm ${tone(w.net_pnl)}`}>{signed(w.net_pnl)} ({pct(w.roi, 2)})</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground num">
                <div>{t("Cash")}<div className="text-foreground">{usd(w.cash)}</div></div>
                <div>{t("Committed")}<div className="text-foreground">{usd(w.committed)}</div></div>
                <div>{t("Fees")}<div className="text-foreground">{usd(w.fees, 3)}</div></div>
                <div>{t("Open")}<div className="text-foreground">{w.open_positions}</div></div>
                <div>{t("Closed")}<div className="text-foreground">{w.closed_trades}</div></div>
                <div>{t("Max DD")}<div className="text-foreground">{usd(w.max_drawdown)}</div></div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={w.status} />
                <Verdict e={w.evidence} />
              </div>
              <div className="text-xs text-muted-foreground">{w.strategies.join(", ")}</div>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="mt-6">
        <Tabs value={tab} onValueChange={setTab} className="mb-2">
          <TabsList>
            <TabsTrigger value="open">{t("Open positions")}</TabsTrigger>
            <TabsTrigger value="closed">{t("Closed positions")}</TabsTrigger>
          </TabsList>
        </Tabs>
        <DataTable data={rows} columns={posColumns} empty={tab === "open" ? t("No open positions in this wallet.") : t("No closed positions yet.")} />
      </div>
    </>
  )
}
