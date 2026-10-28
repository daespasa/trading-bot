"use client"
import { GraduationCap, Languages, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { useTour } from "@/components/tour/tour"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { useLive } from "@/hooks/use-live"
import { setLang, t, useLang } from "@/lib/i18n"

export function TopBar() {
  const live = useLive()
  const lang = useLang()
  const tour = useTour()
  const { resolvedTheme, setTheme } = useTheme()
  const h = live.health
  const state = !live.connected
    ? { label: t("Dashboard disconnected"), cls: "bg-muted-foreground" }
    : !h
      ? { label: t("Connecting…"), cls: "bg-muted-foreground animate-pulse" }
      : h.feed_healthy
      ? { label: t("Market feed live, {n} markets", { n: h.markets ?? 0 }), cls: "bg-gain animate-pulse" }
      : { label: t("Market feed down, new trades halted"), cls: "bg-loss" }
  return (
    <header className="sticky top-0 z-10 flex h-12 items-center gap-2 border-b bg-background/90 px-3 backdrop-blur">
      <SidebarTrigger />
      <Separator orientation="vertical" className="h-5" />
      <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-tour="feed">
        <span className={`size-2 rounded-full ${state.cls}`} aria-hidden />
        {state.label}
      </div>
      <div className="ml-auto flex items-center gap-1" data-tour="topbar-actions">
        <Button variant="ghost" size="sm" onClick={tour.start}>
          <GraduationCap /> <span className="hidden sm:inline">{t("Tutorial")}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t("Change language")}
          onClick={() => setLang(lang === "es" ? "en" : "es")}
        >
          <Languages /> {lang === "es" ? "ES" : "EN"}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("Toggle colour theme")}
          onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
        >
          <Sun className="hidden dark:block" />
          <Moon className="dark:hidden" />
        </Button>
      </div>
    </header>
  )
}
