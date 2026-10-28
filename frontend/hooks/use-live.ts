"use client"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { toast } from "sonner"
import { wsUrl } from "@/lib/api"

export type Live = {
  connected: boolean
  ts?: number
  health?: Record<string, unknown> & { feed_healthy?: boolean; markets?: number; trading_halted?: boolean }
}

// Backend change groups -> query keys to refresh. The backend batches these every 2 s.
const KEYS: Record<string, string[][]> = {
  wallets: [["overview"], ["wallets"], ["analytics"]],
  orders: [["trades"], ["overview"], ["profitability"]],
  positions: [["positions"], ["wallets"]],
  opportunities: [["opportunities"], ["overview"]],
  predictions: [["ml"]],
  models: [["ml"]],
  markets: [["markets"]],
  reports: [["reports"]],
  backtests: [["backtests"]],
  events: [["system"]],
  validation: [["profitability"]],
}

export function useLiveConnection() {
  const qc = useQueryClient()
  useEffect(() => {
    let ws: WebSocket | null = null
    let retry = 1000
    let closed = false
    let timer: ReturnType<typeof setTimeout>
    const seen = new Set<string>()
    const connect = () => {
      ws = new WebSocket(wsUrl())
      ws.onopen = () => {
        retry = 1000
        qc.setQueryData<Live>(["live"], (o) => ({ ...o, connected: true }))
      }
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        qc.setQueryData<Live>(["live"], { connected: true, ts: msg.ts, health: msg.health })
        for (const group of msg.changed ?? []) for (const key of KEYS[group] ?? []) qc.invalidateQueries({ queryKey: key })
        for (const n of msg.notifications ?? []) {
          const id = `${n.ts}${n.message}`
          if (seen.has(id)) continue
          seen.add(id)
          if (Date.now() - Date.parse(n.ts) > 10000) continue
          const fn = n.level === "error" ? toast.error : n.level === "warning" ? toast.warning : toast
          fn(n.message)
        }
      }
      ws.onclose = () => {
        qc.setQueryData<Live>(["live"], (o) => ({ ...o, connected: false }))
        if (!closed) timer = setTimeout(connect, (retry = Math.min(retry * 2, 30000)))
      }
    }
    connect()
    return () => {
      closed = true
      clearTimeout(timer)
      ws?.close()
    }
  }, [qc])
}

export function useLive(): Live {
  return useQuery<Live>({ queryKey: ["live"], queryFn: () => ({ connected: false }), staleTime: Infinity, enabled: false })
    .data ?? { connected: false }
}
