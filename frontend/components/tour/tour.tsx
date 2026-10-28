"use client"
import { Search, X } from "lucide-react"
import { usePathname, useRouter } from "next/navigation"
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { t, useLang } from "@/lib/i18n"

type Step = { path: string; target?: string; title: string; body: string; zoom?: number }

// Targets are elements marked with data-tour="…". A missing target falls back to a centred card.
const STEPS: Step[] = [
  { path: "/", title: "Welcome to the lab", body: "This tour shows you around in about two minutes. Everything here uses fake money: the goal is to find out, honestly, whether any strategy could beat Polymarket's prices." },
  { path: "/", target: "nav", title: "Navigation", body: "Each section of the lab lives here. You can collapse this bar with the button at the top left." },
  { path: "/", target: "feed", title: "Is the data live?", body: "Green means prices are streaming in real time. If it turns red, the lab stops opening new bets until the data comes back." },
  { path: "/", target: "stats", zoom: 1.7, title: "Key figures", body: "Balance, profit, win rate and the worst drop. PnL means profit and loss; drawdown is how far the money fell from its best point." },
  { path: "/", target: "equity", zoom: 1.6, title: "Money over time", body: "One line per wallet. All of them see the same markets at the same time, so they can be compared fairly." },
  { path: "/", target: "verdicts", zoom: 1.9, title: "The verdict", body: "This badge is the most important thing on the page. A gain only counts once there are 30+ finished trades and it is very unlikely to be luck." },
  { path: "/", target: "summary", zoom: 1.5, title: "The summary in plain words", body: "At the end of the page: what is going on, without jargon. It is rewritten every time the numbers change." },
  { path: "/profitability", target: "page-title", title: "Profitability Center", body: "The honest answer to 'are we making money?': results per strategy, under three execution models, checked against the real exchange. Gains stay grey until they are statistically supported." },
  { path: "/paper-trading", target: "wallets", zoom: 1.5, title: "Virtual wallets", body: "Each wallet has 100 fake USDC and follows its own strategies. Click one to see its open and finished bets." },
  { path: "/paper-trading", target: "page-actions", title: "Create or restart", body: "Create your own wallet with the strategies you want, or archive everything and start a fresh experiment." },
  { path: "/opportunities", target: "table", zoom: 1.6, title: "Opportunities", body: "Every chance the strategies spotted, including the ones thrown away. The Why column explains each rejection: fees, too little liquidity, risk limits." },
  { path: "/ai", target: "models", zoom: 1.5, title: "AI models", body: "The models are compared with the market's own prices. If they do not beat them on data they have never seen, they are not allowed to bet." },
  { path: "/strategies", target: "strategy-cards", zoom: 1.5, title: "Strategies", body: "Switch strategies on or off and tune their numbers. Changes apply within seconds." },
  { path: "/analytics", target: "analytics-tabs", title: "Analytics", body: "Deeper charts, daily and weekly reports, and backtests that replay recorded data without touching the live wallets." },
  { path: "/settings", target: "exports", title: "Export your data", body: "Download everything as CSV to check the results yourself." },
  { path: "/", target: "topbar-actions", title: "That's it", body: "Restart this tour any time with the graduation-cap button, and switch language or theme right next to it." },
]

const DONE_KEY = "tourDone"
const Ctx = createContext<{ start: () => void }>({ start: () => {} })
export const useTour = () => useContext(Ctx)

export function TourProvider({ children }: { children: React.ReactNode }) {
  const [step, setStep] = useState<number | null>(null)
  useEffect(() => {
    let done = true
    try {
      done = localStorage.getItem(DONE_KEY) === "1"
    } catch {}
    if (!done) {
      const id = setTimeout(() => setStep(0), 800)
      return () => clearTimeout(id)
    }
  }, [])
  const end = useCallback(() => {
    try {
      localStorage.setItem(DONE_KEY, "1")
    } catch {}
    setStep(null)
  }, [])
  return (
    <Ctx.Provider value={{ start: () => setStep(0) }}>
      {children}
      {step !== null && <Overlay index={step} go={setStep} end={end} />}
    </Ctx.Provider>
  )
}

type Rect = { top: number; left: number; width: number; height: number }
const PAD = 8
const LENS = 210

function Overlay({ index, go, end }: { index: number; go: (i: number) => void; end: () => void }) {
  useLang() // re-render when the language changes
  const step = STEPS[index]
  const router = useRouter()
  const path = usePathname()
  // Position and mouse focus are tagged with the step they belong to, so a new step starts clean.
  const [pos, setPos] = useState<{ i: number; r: Rect } | null>(null)
  const [mouse, setMouse] = useState<{ i: number; x: number; y: number } | null>(null)
  const rect = pos?.i === index ? pos.r : null
  const focus = mouse?.i === index ? mouse : null
  const [vw, setVw] = useState(1280)
  const [vh, setVh] = useState(800)
  const elRef = useRef<HTMLElement | null>(null)
  const lensRef = useRef<HTMLDivElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const last = index === STEPS.length - 1

  // Navigate to the step's page, wait for its target, scroll it into view and track its position.
  useEffect(() => {
    if (path !== step.path) router.push(step.path)
    elRef.current = null
    if (!step.target) return
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    let tries = 0
    let raf = 0
    const measure = () => {
      const el = elRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      setPos({ i: index, r: { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 } })
      setVw(window.innerWidth)
      setVh(window.innerHeight)
    }
    const onMove = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    }
    const poll = setInterval(() => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`)
      if (el && el.getBoundingClientRect().width > 0) {
        clearInterval(poll)
        elRef.current = el
        el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" })
        setTimeout(measure, reduce ? 0 : 400)
      } else if (++tries > 40) clearInterval(poll) // not on this screen size: show a centred card
    }, 100)
    window.addEventListener("resize", onMove)
    window.addEventListener("scroll", onMove, true)
    return () => {
      clearInterval(poll)
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", onMove)
      window.removeEventListener("scroll", onMove, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index])

  // Magnifier content: a static copy of the highlighted element, scaled inside the lens.
  useLayoutEffect(() => {
    const lens = lensRef.current
    const el = elRef.current
    if (!lens || !el || !rect || !step.zoom) return
    const clone = el.cloneNode(true) as HTMLElement
    clone.removeAttribute("data-tour")
    lens.replaceChildren(clone)
  }, [rect, step.zoom])

  let lensTransform = ""
  if (rect && step.zoom) {
    const z = step.zoom
    const f = focus ?? { x: 0, y: 0 }
    lensTransform = `translate(${LENS / 2 - (f.x - rect.left - PAD) * z}px, ${LENS / 2 - (f.y - rect.top - PAD) * z}px) scale(${z})`
  }

  useEffect(() => {
    cardRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") end()
      else if (e.key === "ArrowRight") {
        if (last) end()
        else go(index + 1)
      }
      else if (e.key === "ArrowLeft" && index > 0) go(index - 1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [index, last, go, end])

  const inside = (x: number, y: number) =>
    rect && x >= rect.left && x <= rect.left + rect.width && y >= rect.top && y <= rect.top + rect.height

  // Card below the target when there is room, otherwise above; centred when there is no target.
  const cardW = Math.min(380, vw - 32)
  let cardStyle: React.CSSProperties = { left: "50%", top: "50%", transform: "translate(-50%, -50%)", width: cardW }
  if (rect) {
    const CARD_H = 250
    const below = rect.top + rect.height + 12
    const left = Math.min(Math.max(16, rect.left), vw - cardW - 16)
    if (below + CARD_H < vh) cardStyle = { left, top: below, width: cardW }
    else if (rect.top - CARD_H - 12 > 0) cardStyle = { left, top: rect.top - CARD_H - 12, width: cardW }
    else cardStyle = { right: 16, bottom: 16, width: cardW } // target fills the screen: least intrusive corner
  }
  // The lens only appears while the pointer is over the highlighted area, centred on it.
  const lensPos = focus && inside(focus.x, focus.y) ? { left: focus.x - LENS / 2, top: focus.y - LENS / 2 } : null

  return (
    <div
      className="fixed inset-0 z-[100]"
      onMouseMove={(e) => {
        if (step.zoom) setMouse({ i: index, x: e.clientX, y: e.clientY })
      }}
    >
      {rect ? (
        <div
          aria-hidden
          className="pointer-events-none absolute rounded-xl ring-2 ring-primary transition-all duration-300 motion-reduce:transition-none"
          style={{ ...rect, boxShadow: "0 0 0 9999px rgb(4 7 14 / 0.72)" }}
        />
      ) : (
        <div aria-hidden className="absolute inset-0 bg-[rgb(4_7_14/0.72)]" />
      )}

      {step.zoom && rect && (
        <div
          aria-hidden
          className="pointer-events-none absolute overflow-hidden rounded-full border-2 border-primary bg-background shadow-2xl"
          style={{ ...(lensPos ?? { left: 0, top: 0 }), width: LENS, height: LENS, visibility: lensPos ? "visible" : "hidden" }}
        >
          <div
            ref={lensRef}
            className="pointer-events-none absolute top-0 left-0 origin-top-left"
            style={{ width: rect.width - PAD * 2, transform: lensTransform }}
          />
          <Search className="absolute right-6 bottom-6 size-5 rounded-full bg-primary p-1 text-primary-foreground" />
        </div>
      )}

      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        tabIndex={-1}
        className="absolute rounded-lg border bg-popover p-4 text-popover-foreground shadow-2xl outline-none"
        style={cardStyle}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="text-xs text-muted-foreground num">{t("Step {i} of {n}", { i: index + 1, n: STEPS.length })}</div>
          <button type="button" onClick={end} aria-label={t("Close tutorial")} className="text-muted-foreground hover:text-foreground">
            <X className="size-4" />
          </button>
        </div>
        <h2 id="tour-title" className="mt-1 text-base font-semibold">{t(step.title)}</h2>
        <p className="mt-1.5 text-sm text-muted-foreground">{t(step.body)}</p>
        {step.zoom && rect && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-primary">
            <Search className="size-3.5" /> {t("Move the mouse over the highlighted area to magnify it.")}
          </p>
        )}
        <div className="mt-4 flex items-center gap-2">
          <div className="flex gap-1" aria-hidden>
            {STEPS.map((_, i) => (
              <span key={i} className={`h-1.5 rounded-full transition-all ${i === index ? "w-4 bg-primary" : "w-1.5 bg-muted-foreground/40"}`} />
            ))}
          </div>
          <div className="ml-auto flex gap-2">
            {index > 0 && (
              <Button size="sm" variant="ghost" onClick={() => go(index - 1)}>{t("Back")}</Button>
            )}
            <Button size="sm" onClick={() => (last ? end() : go(index + 1))}>{last ? t("Finish") : t("Next")}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
