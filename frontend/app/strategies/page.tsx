"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { PageHeader } from "@/components/dashboard/stat"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api, put } from "@/lib/api"
import { t } from "@/lib/i18n"

type S = {
  name: string; family: string; description: string; enabled: boolean
  config: Record<string, number>; defaults: Record<string, number>; opportunities_7d: Record<string, number>
}

const FAMILY = (): Record<string, string> => ({ arbitrage: t("Arbitrage"), rule: t("Rule-based"), ml: t("Machine learning") })

function StrategyCard({ s }: { s: S }) {
  const qc = useQueryClient()
  const [cfg, setCfg] = useState<Record<string, string>>(Object.fromEntries(Object.entries(s.config).map(([k, v]) => [k, String(v)])))
  const save = useMutation({
    mutationFn: (body: object) => put(`/strategies/${s.name}`, body),
    onSuccess: () => { toast.success(t("Saved {n}", { n: s.name })); qc.invalidateQueries({ queryKey: ["strategies"] }) },
    onError: (e) => toast.error(String(e)),
  })
  const dirty = Object.entries(cfg).some(([k, v]) => Number(v) !== s.config[k])
  const o = s.opportunities_7d
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">{s.name}</CardTitle>
            <Badge variant="outline" className="mt-1">{FAMILY()[s.family] ?? s.family}</Badge>
          </div>
          <Switch aria-label={t("Enable {n}", { n: s.name })} checked={s.enabled} onCheckedChange={(v) => save.mutate({ enabled: v })} />
        </div>
        <CardDescription className="max-w-[70ch]">{t(`strategy:${s.name}`) === `strategy:${s.name}` ? s.description : t(`strategy:${s.name}`)}</CardDescription>
        <p className="text-xs text-muted-foreground num">
          {t("Last 7 days: {d} detected, {e} executed, {r} discarded", { d: o.detected ?? 0, e: o.executed ?? 0, r: o.rejected ?? 0 })}
        </p>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {Object.keys(s.defaults).map((k) => (
            <div key={k} className="grid gap-1">
              <Label htmlFor={`${s.name}-${k}`} className="text-xs text-muted-foreground">{t(k.replaceAll("_", " "))}</Label>
              <Input id={`${s.name}-${k}`} type="number" step="any" min={0} className="h-8 num" value={cfg[k] ?? ""} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} />
            </div>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate({ config: Object.fromEntries(Object.entries(cfg).map(([k, v]) => [k, Number(v)])) })}>
            {t("Save parameters")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCfg(Object.fromEntries(Object.entries(s.defaults).map(([k, v]) => [k, String(v)])))}>
            {t("Reset to defaults")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export default function StrategiesPage() {
  const { data = [] } = useQuery({ queryKey: ["strategies"], queryFn: () => api<S[]>("/strategies") })
  return (
    <>
      <PageHeader
        title={t("Strategies")}
        description={t("Each strategy produces signals from the same market state. Wallets subscribe to strategies; risk limits and execution are applied per wallet. Changes apply on the next scan.")}
      />
      <div className="grid gap-4 xl:grid-cols-2" data-tour="strategy-cards">
        {data.map((s) => <StrategyCard key={`${s.name}-${JSON.stringify(s.config)}`} s={s} />)}
      </div>
    </>
  )
}
