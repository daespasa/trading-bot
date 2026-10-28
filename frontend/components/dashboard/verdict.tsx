import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { t, tCode } from "@/lib/i18n"

export type Evidence = { n: number; verdict: string; mean?: number; ci95?: [number, number]; t?: number | null }

/** The lab's signature: every result carries its statistical verdict. */
export function Verdict({ e, className }: { e?: Evidence | null; className?: string }) {
  if (!e) return null
  const v = e.verdict
  const cls = v.startsWith("POSITIVE")
    ? "border-gain/40 bg-gain/10 text-gain"
    : v.startsWith("NEGATIVE")
      ? "border-loss/40 bg-loss/10 text-loss"
      : "border-border bg-muted text-muted-foreground"
  const tr = tCode(v)
  const text = tr !== v ? tr : v.charAt(0) + v.slice(1).toLowerCase()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs whitespace-nowrap", cls, className)}>
          {text}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs num">
        {t("{n} closed trades", { n: e.n })}
        {e.mean != null && <>, {t("mean {v} USDC per trade", { v: e.mean.toFixed(4) })}</>}
        {e.ci95 && <>, {t("95% CI")} [{e.ci95[0].toFixed(4)}, {e.ci95[1].toFixed(4)}]</>}
        . {t("Profit counts as evidence only with 30+ trades and a confidence interval above zero.")}
      </TooltipContent>
    </Tooltip>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase()
  const cls =
    s === "active" || s === "executed" || s === "filled" || s === "done" || s === "running" || s === "open"
      ? "text-gain border-gain/40"
      : s === "rejected" || s === "failed" || s.includes("no demonstrated")
        ? "text-loss border-loss/40"
        : "text-muted-foreground border-border"
  return <span className={cn("inline-flex rounded border px-1.5 py-0.5 text-xs whitespace-nowrap", cls)}>{tCode(status)}</span>
}
