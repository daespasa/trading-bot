"use client"
import { CircleAlert, CircleCheck, CircleMinus, Info, Sparkles } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { explain, type Tone } from "@/lib/explain"
import { t } from "@/lib/i18n"

const ICON: Record<Tone, React.ReactNode> = {
  good: <CircleCheck className="size-4 text-gain" aria-hidden />,
  bad: <CircleMinus className="size-4 text-loss" aria-hidden />,
  warn: <CircleAlert className="size-4 text-chart-3" aria-hidden />,
  neutral: <Info className="size-4 text-muted-foreground" aria-hidden />,
}

export function PlainSummary(props: { data: Parameters<typeof explain>[0] }) {
  const lines = explain(props.data)
  return (
    <Card data-tour="summary">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="size-4 text-primary" aria-hidden /> {t("What is happening, in plain words")}
        </CardTitle>
        <CardDescription>{t("Written automatically from the live numbers by fixed rules, without a language model, so it cannot make up figures.")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-3 md:grid-cols-2">
          {lines.map((l) => (
            <li key={l.title} className="flex gap-2.5">
              <span className="mt-0.5 shrink-0">{ICON[l.tone]}</span>
              <div>
                <div className="text-sm font-medium">{l.title}</div>
                <p className="max-w-[65ch] text-sm text-muted-foreground">{l.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
