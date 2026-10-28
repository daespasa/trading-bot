// All requests go through the Next.js rewrite (/api -> backend), so the browser never needs CORS.
export function token(): string {
  try {
    return localStorage.getItem("apiToken") ?? ""
  } catch {
    return ""
  }
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const t = token()
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body.slice(0, 200)}`)
  }
  return res.json() as Promise<T>
}

export const post = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) })
export const put = <T = unknown>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) })

export function wsUrl(): string {
  const port = process.env.NEXT_PUBLIC_BACKEND_WS_PORT ?? "8000"
  const proto = location.protocol === "https:" ? "wss" : "ws"
  const t = token()
  return `${proto}://${location.hostname}:${port}/ws${t ? `?token=${encodeURIComponent(t)}` : ""}`
}
