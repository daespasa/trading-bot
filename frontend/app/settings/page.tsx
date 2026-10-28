"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { AreaSeries } from "@/components/charts/charts"
import { PageHeader, Stat } from "@/components/dashboard/stat"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api, put, token } from "@/lib/api"
import { ago } from "@/lib/format"
import type { Health, WalletSummary } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

type Settings = {
  runtime: { max_markets: number; retrain_hours: number; retention_days: number; prediction_minutes: number; notifications: boolean; auto_train: boolean; kill_switch: boolean }
  profile: string; max_markets_ceiling: number; snapshot_seconds: number; scan_seconds: number; stale_seconds: number; ml_threads: number
  defaults: Record<string, number>
  scenarios: Record<string, Scn>
}
type Scn = { latency_ms: number; depth_fraction: number; extra_ticks: number; fee_mode: string; edge_margin: number }

function KillSwitch({ on }: { on: boolean }) {
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: (v: boolean) => put("/settings", { kill_switch: v }),
    onSuccess: (_, v) => {
      toast(v ? t("Kill switch on: no new positions will be opened") : t("Kill switch off: trading resumed"))
      qc.invalidateQueries({ queryKey: ["settings"] })
    },
  })
  return (
    <Card className={on ? "border-loss/60" : ""}>
      <CardHeader>
        <CardTitle className="text-base">{t("Kill switch")}</CardTitle>
        <CardDescription>{t("Stops every wallet from opening new positions immediately. Opportunities are still detected and logged, and existing exits keep running.")}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-3">
        <Switch checked={on} onCheckedChange={(v) => m.mutate(v)} aria-label={t("Kill switch")} />
        <span className={on ? "font-medium text-loss" : "text-muted-foreground"}>{on ? t("Trading stopped") : t("Trading allowed")}</span>
      </CardContent>
    </Card>
  )
}

function ScenarioEditor({ scenarios }: { scenarios: Record<string, Scn> }) {
  const qc = useQueryClient()
  const [v, setV] = useState(scenarios)
  const save = useMutation({
    mutationFn: () => put("/settings", { scenarios: v }),
    onSuccess: () => { toast.success(t("Execution models saved")); qc.invalidateQueries({ queryKey: ["settings"] }) },
    onError: (e) => toast.error(String(e)),
  })
  const field = (name: string, k: keyof Scn, step = "1") => (
    <Input
      type="number" step={step} min={0} className="h-8 w-24 num" aria-label={`${name} ${k}`}
      value={String(v[name][k])} onChange={(e) => setV({ ...v, [name]: { ...v[name], [k]: Number(e.target.value) } })}
    />
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("Execution scenarios")}</CardTitle>
        <CardDescription>{t("Assumptions used to simulate fills. Changing them affects new orders only; recorded results are never rewritten.")}</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-sm num">
          <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">{t("Scenario")}</th><th className="text-left font-normal">{t("Latency")} (ms)</th><th className="text-left font-normal">{t("Depth taken")}</th><th className="text-left font-normal">{t("Adverse ticks")}</th><th className="text-left font-normal">{t("Safety margin")}</th><th className="text-left font-normal">{t("Fees")}</th></tr></thead>
          <tbody>
            {Object.keys(v).map((k) => (
              <tr key={k} className="border-b last:border-0">
                <td className="py-1.5 pr-2">{tCode(k).toLowerCase()}</td>
                <td className="pr-2">{field(k, "latency_ms")}</td>
                <td className="pr-2">{field(k, "depth_fraction", "0.05")}</td>
                <td className="pr-2">{field(k, "extra_ticks")}</td>
                <td className="pr-2">{field(k, "edge_margin", "0.005")}</td>
                <td>{t(v[k].fee_mode)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Button size="sm" className="mt-3" onClick={() => save.mutate()} disabled={save.isPending}>{t("Save execution models")}</Button>
      </CardContent>
    </Card>
  )
}

type Sys = { health: Health; resources: { ts: string; rss_mb: number; training_rss_mb: number; cpu_pct: number; sys_mem_avail_mb: number; db_mb: number }[]; events: { ts: string; level: string; kind: string; message: string }[] }

function Runtime({ s }: { s: Settings }) {
  const qc = useQueryClient()
  const [v, setV] = useState(s.runtime)
  const save = useMutation({
    mutationFn: () => put("/settings", v),
    onSuccess: () => { toast.success(t("Settings saved")); qc.invalidateQueries({ queryKey: ["settings"] }) },
  })
  const num = (k: keyof Settings["runtime"], label: string, hint?: string) => (
    <div className="grid gap-1">
      <Label htmlFor={k}>{t(label)}</Label>
      <Input id={k} type="number" min={1} className="h-8 num" value={String(v[k])} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} />
      {hint && <span className="text-xs text-muted-foreground">{t(hint)}</span>}
    </div>
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("Monitoring and training")}</CardTitle>
        <CardDescription>{t("Resource profile {p}: snapshots every {a}s, scans every {b}s, books older than {c}s are not tradable, training uses {d} threads.", { p: s.profile, a: s.snapshot_seconds, b: s.scan_seconds, c: s.stale_seconds, d: s.ml_threads })}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        {num("max_markets", "Markets monitored", t("Up to {n} in this profile. Applies at the next discovery.", { n: s.max_markets_ceiling }))}
        {num("retention_days", "Snapshot retention (days)")}
        {num("retrain_hours", "Retrain every (hours)")}
        {num("prediction_minutes", "Prediction interval (minutes)", "Applies after a restart.")}
        <label className="flex items-center justify-between text-sm">{t("Automatic training")}<Switch checked={v.auto_train} onCheckedChange={(x) => setV({ ...v, auto_train: x })} /></label>
        <label className="flex items-center justify-between text-sm">{t("Notifications")}<Switch checked={v.notifications} onCheckedChange={(x) => setV({ ...v, notifications: x })} /></label>
        <div><Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>{t("Save settings")}</Button></div>
      </CardContent>
    </Card>
  )
}

function Limits({ w }: { w: WalletSummary }) {
  const [v, setV] = useState({ max_trade_usdc: w.max_trade_usdc, max_open_positions: w.max_open_positions, max_daily_loss: w.max_daily_loss })
  const save = useMutation({ mutationFn: () => put(`/wallets/${w.id}`, v), onSuccess: () => toast.success(t("Limits saved for {n}", { n: w.name })), onError: (e) => toast.error(String(e)) })
  return (
    <div className="grid grid-cols-2 items-end gap-2 md:grid-cols-5">
      <div className="col-span-2 text-sm md:col-span-1">{w.name}</div>
      {(["max_trade_usdc", "max_open_positions", "max_daily_loss"] as const).map((k) => (
        <div key={k} className="grid gap-1">
          <Label className="text-xs text-muted-foreground" htmlFor={`${w.id}${k}`}>{t(k.replaceAll("_", " "))}</Label>
          <Input id={`${w.id}${k}`} type="number" min={1} className="h-8 num" value={v[k]} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} />
        </div>
      ))}
      <Button size="sm" variant="outline" onClick={() => save.mutate()}>{t("Save limits")}</Button>
    </div>
  )
}

export default function SettingsPage() {
  const { data } = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/settings") })
  const { data: sys } = useQuery({ queryKey: ["system"], queryFn: () => api<Sys>("/system"), refetchInterval: 30_000 })
  const { data: wallets = [] } = useQuery({ queryKey: ["wallets"], queryFn: () => api<WalletSummary[]>("/wallets") })
  const [tok, setTok] = useState(() => (typeof window === "undefined" ? "" : token()))
  const last = sys?.resources.at(-1)
  const tokQs = token() ? `&token=${encodeURIComponent(token())}` : ""
  return (
    <>
      <PageHeader title={t("Settings")} description={t("Real trading is permanently disabled. No keys, wallets or signatures exist anywhere in this system.")} />
      {data && <div className="mb-4"><KillSwitch on={!!data.runtime.kill_switch} /></div>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t("Backend memory")} value={last ? `${last.rss_mb.toFixed(0)} MB` : "—"} sub={last?.training_rss_mb ? t("training {n} MB", { n: last.training_rss_mb.toFixed(0) }) : t("no training running")} />
        <Stat label={t("Backend CPU")} value={last ? `${last.cpu_pct.toFixed(0)}%` : "—"} />
        <Stat label={t("System memory free")} value={last ? `${(last.sys_mem_avail_mb / 1024).toFixed(1)} GB` : "—"} />
        <Stat label={t("Database")} value={last ? `${last.db_mb.toFixed(1)} MB` : "—"} />
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Backend memory, last 2 h")}</CardTitle></CardHeader>
        <CardContent><AreaSeries data={sys?.resources ?? []} x="ts" y="rss_mb" label={t("RSS MB")} height={160} /></CardContent>
      </Card>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {data && <Runtime key={JSON.stringify(data.runtime)} s={data} />}
        {data && <ScenarioEditor key={JSON.stringify(data.scenarios)} scenarios={data.scenarios} />}
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Risk limits per wallet")}</CardTitle></CardHeader>
        <CardContent className="space-y-3">{wallets.map((w) => <Limits key={w.id} w={w} />)}</CardContent>
      </Card>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader data-tour="exports"><CardTitle className="text-base">{t("Export data")}</CardTitle><CardDescription>{t("CSV files for analysis elsewhere. Snapshots export the last 7 days.")}</CardDescription></CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {["markets", "opportunities", "orders", "positions", "equity", "predictions", "models", "snapshots", "events", "wallets"].map((x) => (
              <Button key={x} asChild size="sm" variant="outline"><a href={`/api/export/${x}.csv?days=7${tokQs}`}><Download /> {t(x)}</a></Button>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">{t("API token")}</CardTitle><CardDescription>{t("Only needed when the backend runs with API_TOKEN set, for example when exposed beyond this machine.")}</CardDescription></CardHeader>
          <CardContent className="flex gap-2">
            <Input type="password" value={tok} onChange={(e) => setTok(e.target.value)} aria-label={t("API token")} className="h-8" />
            <Button size="sm" onClick={() => { localStorage.setItem("apiToken", tok); location.reload() }}>{t("Save token")}</Button>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("System events")}</CardTitle></CardHeader>
        <CardContent className="max-h-80 space-y-1 overflow-auto text-sm">
          {sys?.events.map((e, i) => (
            <div key={i} className="flex gap-3">
              <span className="w-16 shrink-0 text-xs text-muted-foreground num">{ago(e.ts)}</span>
              <span className={e.level === "error" ? "text-loss" : e.level === "warning" ? "text-chart-3" : "text-muted-foreground"}>{e.kind}</span>
              <span className="min-w-0 truncate">{e.message}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  )
}
