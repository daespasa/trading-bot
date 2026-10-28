"use client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { Fragment, useEffect, useState } from "react"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { TourProvider } from "@/components/tour/tour"
import { useLiveConnection } from "@/hooks/use-live"
import { setLang, storedLang, useLang } from "@/lib/i18n"

function Live() {
  useLiveConnection()
  return null
}

// Remounting the UI on a language change refreshes every translated string, including table cells.
function LangScope({ children }: { children: React.ReactNode }) {
  const lang = useLang()
  // Hydrate with the server language, then switch to the stored choice (avoids a hydration mismatch).
  useEffect(() => setLang(storedLang()), [])
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  return <Fragment key={lang}>{children}</Fragment>
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false, retry: 1 } } }),
  )
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <Live />
          <TourProvider>
            <LangScope>{children}</LangScope>
          </TourProvider>
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
