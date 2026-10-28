// Plain-language summary of the lab's state, written by rules from real numbers (no language model):
// every figure in the text comes straight from the API, so it cannot invent results.
import type { Evidence } from "@/components/dashboard/verdict"
import { t } from "@/lib/i18n"
import { pct, signed } from "@/lib/format"

export type Tone = "good" | "bad" | "neutral" | "warn"
export type Line = { tone: Tone; title: string; text: string }

type W = { name: string; strategies: string[]; net_pnl: number; roi: number; closed_trades: number; open_positions: number; evidence: Evidence; max_drawdown: number }
type Input = {
  totals: { initial_capital: number; net_pnl: number; roi: number; closed_trades: number; open_positions: number; max_drawdown: number }
  wallets: W[]
  health: { feed_healthy: boolean; uptime_s: number }
  markets_monitored: number
  models?: { kind: string; feature_set: string; status: string }[]
  ml_experimental?: boolean
}

function what(strats: string[]): string {
  const s = new Set(strats)
  if (s.size >= 5) return t("uses every strategy at once")
  if ([...s].every((x) => x.startsWith("arb") || x === "related_markets"))
    return t("looks for sure bargains: buying every possible outcome for less than it will pay")
  if (s.has("ai_lightgbm") || s.has("ai_logistic"))
    return t("only bets when its AI model has proven it predicts better than the market")
  return t("bets with fixed rules, for example that a fast price move will continue or bounce back")
}

export function explain(d: Input): Line[] {
  const out: Line[] = []
  const hours = d.health.uptime_s / 3600
  const tot = d.totals
  out.push({
    tone: tot.net_pnl > 0 ? "good" : tot.net_pnl < 0 ? "bad" : "neutral",
    title: t("How the money is doing"),
    text: t(
      "The lab started with {cap} fake USDC split across {n} wallets. Overall it is {pnl} USDC ({roi}). None of this is real money.",
      { cap: tot.initial_capital.toFixed(0), n: d.wallets.length, pnl: signed(tot.net_pnl), roi: pct(tot.roi, 1) },
    ),
  })

  const decided = d.wallets.filter((w) => w.evidence.verdict.startsWith("POSITIVE") || w.evidence.verdict.startsWith("NEGATIVE"))
  const winners = d.wallets.filter((w) => w.evidence.verdict.startsWith("POSITIVE"))
  out.push(
    winners.length
      ? { tone: "good", title: t("Is anything really working?"), text: t("{names} show a statistically solid profit: enough trades, and a result unlikely to be luck. Keep watching that it lasts.", { names: winners.map((w) => w.name).join(", ") }) }
      : {
          tone: decided.length ? "bad" : "neutral",
          title: t("Is anything really working?"),
          text: decided.length
            ? t("No strategy has proven it makes money, and at least one shows a solid loss. That is a valid result too: it tells us what does not work.")
            : t("Too early to tell. A strategy only counts as working after 30 or more finished trades with a result that cannot be explained by luck. So far there are {n} finished trades in total.", { n: tot.closed_trades }),
        },
  )

  const active = d.wallets.filter((w) => w.closed_trades + w.open_positions > 0)
  if (active.length) {
    const best = [...active].sort((a, b) => b.net_pnl - a.net_pnl)[0]
    const worst = [...active].sort((a, b) => a.net_pnl - b.net_pnl)[0]
    out.push({
      tone: best.net_pnl > 0 ? "good" : "neutral",
      title: t("Best and worst"),
      text:
        best === worst
          ? t("Only {b} has traded so far ({pb} USDC). It {wb}.", { b: best.name, pb: signed(best.net_pnl), wb: what(best.strategies) })
          : t("{b} is ahead ({pb} USDC): it {wb}. {w} is behind ({pw} USDC): it {ww}.", {
              b: best.name, pb: signed(best.net_pnl), wb: what(best.strategies),
              w: worst.name, pw: signed(worst.net_pnl), ww: what(worst.strategies),
            }),
    })
  }

  // Explain lines that look frozen or duplicated in the equity chart.
  const idle = d.wallets.filter((w) => w.closed_trades + w.open_positions === 0)
  const twins: string[] = []
  d.wallets.forEach((w, i) => {
    const o = d.wallets.find((x, j) => j < i && x.closed_trades + x.open_positions > 0 && x.closed_trades === w.closed_trades
      && x.open_positions === w.open_positions && Math.abs(x.net_pnl - w.net_pnl) < 1e-6)
    if (o) twins.push(t("{a} has made exactly the same trades as {b}", { a: w.name, b: o.name }))
  })
  if (idle.length || twins.length) {
    const parts = []
    if (idle.length) parts.push(t("{names} have not traded yet, so their line stays flat at the starting balance", { names: idle.map((w) => w.name).join(", ") }))
    parts.push(...twins)
    out.push({
      tone: "neutral",
      title: t("Why only some lines move"),
      text: t("{parts}. A wallet only trades when one of its strategies finds an opportunity that passes every check; wallets that share a strategy see the same signal at the same moment, so they can end up identical. The chart draws those lines dashed or dotted so none is hidden.", { parts: parts.join("; ") }),
    })
  }

  const arb = d.wallets.find((w) => w.strategies.every((s) => s.startsWith("arb") || s === "related_markets"))
  if (arb && arb.closed_trades + arb.open_positions === 0)
    out.push({
      tone: "neutral",
      title: t("Why the arbitrage wallet is idle"),
      text: t("It has not found a single sure bargain. That is normal: Polymarket's own matching engine closes those gaps almost instantly, so free money rarely sits there."),
    })

  if (d.models?.length) {
    const core = d.models.filter((m) => m.feature_set === "core")
    const ok = core.filter((m) => m.status === "ACTIVE")
    out.push(
      ok.length
        ? { tone: "good", title: t("The AI models"), text: t("{n} AI model(s) predicted better than the market on past data and are allowed to bet. Their live bets are the real test.", { n: ok.length }) }
        : d.ml_experimental
          ? { tone: "warn", title: t("The AI models"), text: t("The AI models did not pass validation, but they are betting in experimental mode (switched on in Strategies) to check live whether the validation was right. Their results do not count as a proven edge.") }
        : core.some((m) => m.status === "NO DEMONSTRATED PREDICTIVE EDGE")
          ? { tone: "neutral", title: t("The AI models"), text: t("The AI models were tested on past markets and guessed worse than the market's own prices. So they are not allowed to bet; the AI wallets stay at their starting balance on purpose.") }
          : { tone: "neutral", title: t("The AI models"), text: t("There is not enough finished-market data yet to judge the AI models, so they are not betting.") },
    )
  }

  if (tot.open_positions)
    out.push({ tone: "neutral", title: t("Bets in progress"), text: t("{n} bets are open and waiting for their markets to resolve. Their value changes with the market until then.", { n: tot.open_positions }) })
  if (tot.max_drawdown > 0)
    out.push({ tone: "warn", title: t("Worst slide"), text: t("The worst wallet fell {v} USDC below its best moment. This shows how bumpy the ride is, even if the total ends up fine.", { v: tot.max_drawdown.toFixed(2) }) })

  out.push(
    d.health.feed_healthy
      ? { tone: "good", title: t("Live data"), text: t("Watching {n} markets live for {h} hours. Prices arrive in real time.", { n: d.markets_monitored, h: hours.toFixed(1) }) }
      : { tone: "warn", title: t("Live data"), text: t("The live price feed is down right now, so no new bets are opened until it comes back. This is a safety rule.") },
  )
  return out
}
