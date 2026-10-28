"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BrainCircuit } from "lucide-react"
import { toast } from "sonner"
import { Calibration } from "@/components/charts/charts"
import { type Col, DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/dashboard/stat"
import { StatusBadge } from "@/components/dashboard/verdict"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api, post } from "@/lib/api"
import { ago, dt, pct, prob, signed, tone } from "@/lib/format"
import type { Health } from "@/types/api"
import { t, tCode } from "@/lib/i18n"

type M = Record<string, number | null>
type Model = {
  id: number; kind: string; version: string; feature_set: string; created_at: string; status: string; reason: string
  n_train: number; n_test: number
  metrics: {
    model?: M; market?: M; n_test_markets?: number
    edge?: { delta_logloss: number; se: number | null; t: number | null; clusters: number }
    simulation?: { trades: number; pnl_per_share_set: number; roi: number | null }
    calibration_model?: { mean_pred: number; frac_pos: number; count: number }[]
    calibration_market?: { mean_pred: number; frac_pos: number; count: number }[]
  }
}
type Run = { id: number; started_at: string; finished_at: string | null; status: string; message: string | null; dataset: Record<string, number> }
type Pred = { id: number; ts: string; model_kind: string; question: string; p_model: number; p_market: number; ask: number | null; edge: number | null; diff: number; outcome: number | null }
type LiveM = { model_id: number; kind: string; model: M; market: M }

const predCols: Col<Pred>[] = [
  { id: "ts", header: "Time", accessorFn: (p) => p.ts, cell: (c) => ago(c.getValue()) },
  { id: "k", header: "Model", accessorFn: (p) => p.model_kind },
  { id: "q", header: "Market", accessorFn: (p) => p.question, cell: (c) => <span className="block max-w-[26rem] truncate" title={c.getValue()}>{c.getValue()}</span> },
  { id: "pm", header: "Model P(yes)", accessorFn: (p) => p.p_model, cell: (c) => <span className="text-model">{prob(c.getValue())}</span> },
  { id: "pk", header: "Market P(yes)", accessorFn: (p) => p.p_market, cell: (c) => prob(c.getValue()) },
  { id: "d", header: "Difference", accessorFn: (p) => p.diff, cell: (c) => <span className={tone(c.getValue())}>{signed(c.getValue() * 100, 1)} pp</span> },
  { id: "a", header: "Ask", accessorFn: (p) => p.ask, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toFixed(3)) },
  { id: "e", header: "Edge after fee", accessorFn: (p) => p.edge, cell: (c) => <span className={tone(c.getValue())}>{c.getValue() == null ? "—" : signed(c.getValue(), 3)}</span> },
  { id: "o", header: "Outcome", accessorFn: (p) => p.outcome, cell: (c) => (c.getValue() == null ? t("pending") : c.getValue() === 1 ? t("Yes") : c.getValue() === 0 ? t("No") : "50/50") },
]

function MetricRow({ label, a, b, digits = 4, lowerBetter = true }: { label: string; a?: number | null; b?: number | null; digits?: number; lowerBetter?: boolean }) {
  const better = a != null && b != null && (lowerBetter ? a < b : a > b)
  return (
    <tr className="border-b last:border-0">
      <td className="py-1.5 text-muted-foreground">{t(label)}</td>
      <td className={`py-1.5 text-right num ${better ? "text-gain" : ""}`}>{a == null ? "—" : a.toFixed(digits)}</td>
      <td className="py-1.5 text-right num">{b == null ? "—" : b.toFixed(digits)}</td>
    </tr>
  )
}

function ModelCard({ m }: { m: Model }) {
  const x = m.metrics
  return (
    <Card>
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{m.kind === "lightgbm" ? "LightGBM" : m.kind === "logistic" ? t("Logistic regression") : m.kind}, {t("{fs} features", { fs: m.feature_set })}</CardTitle>
          <StatusBadge status={m.status} />
        </div>
        <p className="text-xs text-muted-foreground">{t("Trained {d}, {n} samples, {k} out-of-sample rows", { d: dt(m.created_at), n: m.n_train, k: m.n_test })}{x.n_test_markets ? ` ${t("across {n} markets", { n: x.n_test_markets })}` : ""}</p>
        <p className="text-xs text-muted-foreground">{m.reason}</p>
      </CardHeader>
      <CardContent>
        {x.model ? (
          <>
            <table className="w-full text-sm">
              <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">{t("Out of sample")}</th><th className="text-right font-normal">{t("Model")}</th><th className="text-right font-normal">{t("Market price")}</th></tr></thead>
              <tbody>
                <MetricRow label="Log loss" a={x.model.log_loss} b={x.market?.log_loss} />
                <MetricRow label="Brier score" a={x.model.brier} b={x.market?.brier} />
                <MetricRow label="Calibration error" a={x.model.ece} b={x.market?.ece} />
                <MetricRow label="Accuracy" a={x.model.accuracy} b={x.market?.accuracy} digits={3} lowerBetter={false} />
                <MetricRow label="Precision" a={x.model.precision} b={x.market?.precision} digits={3} lowerBetter={false} />
                <MetricRow label="Recall" a={x.model.recall} b={x.market?.recall} digits={3} lowerBetter={false} />
              </tbody>
            </table>
            {x.edge && (
              <p className="mt-3 text-xs text-muted-foreground num">
                {t("Log-loss gain over market {g} per prediction, t = {t} over {n} markets.", { g: signed(x.edge.delta_logloss, 4), t: x.edge.t?.toFixed(2) ?? "—", n: x.edge.clusters })}
                {x.simulation && <> {t("Simulated rule: {n} trades, ROI {r} after assumed spread and fees.", { n: x.simulation.trades, r: pct(x.simulation.roi, 1) })}</>}
              </p>
            )}
            <div className="mt-3">
              <Calibration sets={{ [t("Model")]: x.calibration_model ?? [], [t("Market")]: x.calibration_market ?? [] }} height={220} />
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{t("No out-of-sample evaluation was possible with the data available.")}</p>
        )}
      </CardContent>
    </Card>
  )
}

export default function AIPage() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ["ml", "models"], queryFn: () => api<{ models: Model[]; runs: Run[]; health: Health }>("/ml/models?limit=40") })
  const { data: preds = [] } = useQuery({ queryKey: ["ml", "preds"], queryFn: () => api<Pred[]>("/ml/predictions?limit=1000") })
  const { data: live = [] } = useQuery({ queryKey: ["ml", "live"], queryFn: () => api<LiveM[]>("/ml/live-metrics") })
  const train = useMutation({
    mutationFn: () => post<{ result: string }>("/ml/train"),
    onSuccess: (r) => { toast(r.result); qc.invalidateQueries({ queryKey: ["ml"] }) },
  })
  const latest = new Map<string, Model>()
  for (const m of data?.models ?? []) if (!latest.has(`${m.kind}/${m.feature_set}`)) latest.set(`${m.kind}/${m.feature_set}`, m)
  const run = data?.runs[0]
  const active = [...latest.values()].filter((m) => m.status === "ACTIVE")
  return (
    <>
      <PageHeader
        title={t("AI predictions")}
        description={t("Can a model estimate P(yes) better than the market price? Models are trained on resolved Polymarket markets with walk-forward validation and only trade after beating the market out of sample.")}
        actions={
          <Button size="sm" onClick={() => train.mutate()} disabled={train.isPending || data?.health.training_running}>
            <BrainCircuit /> {data?.health.training_running ? (data.health.training_paused ? t("Training paused") : t("Training…")) : t("Train now")}
          </Button>
        }
      />
      {run && (
        <p className="mb-4 text-sm text-muted-foreground">
          {t("Last training run #{id} {s}, started {a}", { id: run.id, s: tCode(run.status), a: ago(run.started_at) })}
          {run.dataset?.history_rows != null && <>, {t("dataset {r} rows from {m} resolved markets plus {l} live rows", { r: run.dataset.history_rows, m: run.dataset.history_markets, l: run.dataset.live_rows })}</>}.
        </p>
      )}
      {latest.size === 0 ? (
        <Alert><AlertTitle>{t("No models trained yet")}</AlertTitle><AlertDescription>{t("Training starts automatically two minutes after launch, or press Train now. It runs in a separate low-priority process limited to 2 threads.")}</AlertDescription></Alert>
      ) : active.length === 0 ? (
        <Alert className="mb-4"><AlertTitle>{t("No model is trading")}</AlertTitle><AlertDescription>{t("None has passed the out-of-sample gate. The AI wallets stay idle until one does; predictions are still recorded and scored as markets resolve.")}</AlertDescription></Alert>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2" data-tour="models">
        {[...latest.values()].map((m) => <ModelCard key={m.id} m={m} />)}
      </div>
      <Card className="mt-4">
        <CardHeader><CardTitle className="text-base">{t("Live forward test")}</CardTitle></CardHeader>
        <CardContent className="text-sm">
          {live.length === 0 ? (
            <p className="text-muted-foreground">{t("Predictions are scored once their markets resolve. None have resolved yet.")}</p>
          ) : (
            <table className="w-full">
              <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">{t("Model")}</th><th className="text-right font-normal">{t("Resolved predictions")}</th><th className="text-right font-normal">{t("Brier model / market")}</th><th className="text-right font-normal">{t("Log loss model / market")}</th></tr></thead>
              <tbody>
                {live.map((l) => (
                  <tr key={l.model_id} className="border-b last:border-0 num">
                    <td className="py-1.5">{l.kind} #{l.model_id}</td>
                    <td className="text-right">{l.model.n}</td>
                    <td className="text-right">{l.model.brier?.toFixed(4)} / {l.market.brier?.toFixed(4)}</td>
                    <td className="text-right">{l.model.log_loss?.toFixed(4)} / {l.market.log_loss?.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
      <h2 className="mt-6 mb-2 text-base font-semibold">{t("Recent predictions")}</h2>
      <DataTable data={preds} columns={predCols} empty={t("No predictions yet. They run every 15 minutes once a model exists.")} />
    </>
  )
}
