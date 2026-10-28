import { t } from "@/lib/i18n"

export const usd = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`

export const signed = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`

export const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(digits)}%`

export const prob = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`)

export const tone = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-muted-foreground" : v > 0 ? "text-gain" : "text-loss"

export function ago(iso: string | number | null | undefined): string {
  if (iso == null) return "—"
  const ms = typeof iso === "number" ? iso * 1000 : Date.parse(iso)
  const s = Math.max(0, (Date.now() - ms) / 1000)
  if (s < 60) return t("{n}s ago", { n: Math.round(s) })
  if (s < 3600) return t("{n}m ago", { n: Math.round(s / 60) })
  if (s < 86400) return t("{n}h ago", { n: Math.round(s / 3600) })
  return t("{n}d ago", { n: Math.round(s / 86400) })
}

export const dt = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"
