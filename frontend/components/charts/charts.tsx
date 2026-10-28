"use client"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, Scatter, ScatterChart, XAxis, YAxis, ZAxis } from "recharts"
import { type ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { t } from "@/lib/i18n"

const PALETTE = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--muted-foreground)"]
const tick = (iso: string) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })
const tickFull = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const tickTime = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })

/** Several series of {ts, value} merged on a shared minute grid. */
export function MultiLine({
  series,
  height = 280,
  baseline,
}: {
  series: Record<string, { ts: string; value: number }[]>
  height?: number
  baseline?: number
}) {
  const names = Object.keys(series)
  const grid = new Map<string, Record<string, number | string>>()
  names.forEach((n, i) =>
    series[n].forEach((p) => {
      const k = p.ts.slice(0, 16)
      grid.set(k, { ...(grid.get(k) ?? { ts: p.ts }), [`s${i}`]: p.value })
    }),
  )
  const data = [...grid.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)))
  // Overlapping lines hide each other: series identical to an earlier one are drawn dashed and labelled,
  // flat series (never moved) are drawn faint and dotted.
  const sig = names.map((n) => series[n].map((p) => p.value.toFixed(4)).join(","))
  const twinOf = names.map((_, i) => sig.findIndex((x, j) => j < i && x === sig[i]))
  const flat = names.map((n) => new Set(series[n].map((p) => p.value.toFixed(4))).size <= 1)
  const label = (n: string, i: number) =>
    flat[i] ? t("{a} (no change)", { a: n }) : twinOf[i] >= 0 ? t("{a} (same as {b})", { a: n, b: names[twinOf[i]] }) : n
  const config: ChartConfig = Object.fromEntries(names.map((n, i) => [`s${i}`, { label: label(n, i), color: PALETTE[i % PALETTE.length] }]))
  const spanH = data.length ? (Date.parse(String(data[data.length - 1].ts)) - Date.parse(String(data[0].ts))) / 3.6e6 : 0
  const xFmt = spanH < 24 ? tickTime : spanH < 72 ? tickFull : tick
  if (data.length < 2) return <EmptyChart height={height} text={t("Equity is recorded every 5 minutes. The curve appears after the first snapshots.")} />
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <LineChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="ts" tickFormatter={xFmt} minTickGap={48} tickLine={false} axisLine={false} />
        <YAxis domain={["auto", "auto"]} width={52} tickLine={false} axisLine={false} />
        {baseline != null && <ReferenceLine y={baseline} stroke="var(--muted-foreground)" strokeDasharray="4 4" />}
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(_, p) => tickFull(String(p?.[0]?.payload?.ts))} />} />
        <ChartLegend content={<ChartLegendContent />} />
        {names.map((_, i) => (
          <Line
            key={i}
            dataKey={`s${i}`}
            type="linear"
            stroke={`var(--color-s${i})`}
            dot={false}
            strokeWidth={twinOf[i] >= 0 ? 2.5 : 1.75}
            strokeDasharray={flat[i] ? "2 5" : twinOf[i] >= 0 ? "7 6" : undefined}
            strokeOpacity={flat[i] ? 0.6 : 1}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  )
}

export function AreaSeries({ data, x, y, label, color = "var(--chart-1)", height = 200 }: { data: object[]; x: string; y: string; label: string; color?: string; height?: number }) {
  if (data.length < 2) return <EmptyChart height={height} text={t("Not enough data yet.")} />
  return (
    <ChartContainer config={{ [y]: { label, color } }} className="w-full" style={{ height }}>
      <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey={x} tickFormatter={(v) => (String(v).length > 10 ? tick(v) : v)} minTickGap={48} tickLine={false} axisLine={false} />
        <YAxis width={52} tickLine={false} axisLine={false} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Area dataKey={y} type="linear" stroke={`var(--color-${y})`} fill={`var(--color-${y})`} fillOpacity={0.15} isAnimationActive={false} />
      </AreaChart>
    </ChartContainer>
  )
}

/** Bars coloured by sign (gain/loss). */
export function SignedBars({ data, x, y, label, height = 220 }: { data: Record<string, unknown>[]; x: string; y: string; label: string; height?: number }) {
  if (!data.length) return <EmptyChart height={height} text={t("No closed trades yet.")} />
  return (
    <ChartContainer config={{ [y]: { label, color: "var(--chart-1)" } }} className="w-full" style={{ height }}>
      <BarChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey={x} tickLine={false} axisLine={false} minTickGap={16} />
        <YAxis width={52} tickLine={false} axisLine={false} />
        <ReferenceLine y={0} stroke="var(--muted-foreground)" />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey={y} isAnimationActive={false} radius={2}>
          {data.map((d, i) => (
            <Cell key={i} fill={Number(d[y]) >= 0 ? "var(--gain)" : "var(--loss)"} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

/** Reliability diagram: predicted probability vs observed frequency, diagonal = perfect calibration. */
export function Calibration({ sets, height = 260 }: { sets: Record<string, { mean_pred: number; frac_pos: number; count: number }[]>; height?: number }) {
  const names = Object.keys(sets)
  if (!names.some((n) => sets[n]?.length)) return <EmptyChart height={height} text={t("Calibration needs resolved predictions.")} />
  const config: ChartConfig = Object.fromEntries(names.map((n, i) => [`c${i}`, { label: n, color: PALETTE[i] }]))
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <ScatterChart margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis type="number" dataKey="mean_pred" domain={[0, 1]} name={t("Predicted")} tickLine={false} />
        <YAxis type="number" dataKey="frac_pos" domain={[0, 1]} name={t("Observed")} width={40} tickLine={false} />
        <ZAxis type="number" dataKey="count" range={[30, 260]} name={t("Samples")} />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        {names.map((n, i) => (
          <Scatter key={n} name={n} dataKey={`c${i}`} data={sets[n]} fill={`var(--color-c${i})`} line isAnimationActive={false} />
        ))}
      </ScatterChart>
    </ChartContainer>
  )
}

export function Histogram({ values, bins = 20, height = 220 }: { values: number[]; bins?: number; height?: number }) {
  if (values.length < 2) return <EmptyChart height={height} text={t("No closed trades yet.")} />
  const lo = Math.min(...values), hi = Math.max(...values)
  const w = (hi - lo) / bins || 1
  const data = Array.from({ length: bins }, (_, i) => ({ bin: (lo + w * (i + 0.5)).toFixed(3), count: 0 }))
  values.forEach((v) => data[Math.min(bins - 1, Math.floor((v - lo) / w))].count++)
  return (
    <ChartContainer config={{ count: { label: t("Trades"), color: "var(--chart-1)" } }} className="w-full" style={{ height }}>
      <BarChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <XAxis dataKey="bin" tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis width={32} allowDecimals={false} tickLine={false} axisLine={false} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="count" isAnimationActive={false} radius={2}>
          {data.map((d, i) => (
            <Cell key={i} fill={Number(d.bin) >= 0 ? "var(--gain)" : "var(--loss)"} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

export function EmptyChart({ height, text }: { height: number; text: string }) {
  return (
    <div className="flex items-center justify-center rounded-md border border-dashed text-center text-sm text-muted-foreground" style={{ height }}>
      <p className="max-w-sm px-4">{text}</p>
    </div>
  )
}
