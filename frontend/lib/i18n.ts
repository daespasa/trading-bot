"use client"
import { useSyncExternalStore } from "react"
import { ES } from "@/lib/i18n-es"

// English source strings are the keys; Spanish comes from ES and falls back to English when missing.
export type Lang = "es" | "en"
const DEFAULT: Lang = "es"
let current: Lang = DEFAULT
const listeners = new Set<() => void>()

export function storedLang(): Lang {
  try {
    const v = localStorage.getItem("lang")
    return v === "en" || v === "es" ? v : DEFAULT
  } catch {
    return DEFAULT
  }
}

export function setLang(l: Lang) {
  if (l === current) return
  current = l
  try {
    localStorage.setItem("lang", l)
  } catch {}
  document.documentElement.lang = l
  listeners.forEach((f) => f())
}

export function useLang(): Lang {
  return useSyncExternalStore(
    (cb) => (listeners.add(cb), () => listeners.delete(cb)),
    () => current,
    () => DEFAULT,
  )
}

export function t(en: string, vars?: Record<string, string | number>): string {
  let s = (current === "es" && ES[en]) || en
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v))
  return s
}

/** Translate backend enum-like values (status, verdict, scenario...) by known prefixes. */
export function tCode(v: string): string {
  if (current !== "es") return v
  const exact = ES[v] ?? ES[v.toLowerCase()]
  if (exact) return exact
  const m = v.match(/^INSUFFICIENT EVIDENCE \(n=(\d+) < (\d+)\)$/)
  if (m) return t("INSUFFICIENT EVIDENCE (n={n} < {min})", { n: m[1], min: m[2] })
  return v
}
