const pathMatch = window.location.pathname.match(/^\/control\/(codex|claude)(?:\/|$)/)

export const controlBase = pathMatch ? `/control/${pathMatch[1]}` : '/control/codex'
export const CONTROL_REQUEST_TIMEOUT_MS = 8_000

export function controlUrl(path) {
  if (/^\/control\/(codex|claude)(?:\/|$)/.test(path)) return path
  return `${controlBase}${path.startsWith('/') ? path : `/${path}`}`
}

export async function controlFetch(path, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('X-Local-Proxy-Control', '1')
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), CONTROL_REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(controlUrl(path), {
      cache: 'no-store',
      ...options,
      signal: controller.signal,
      headers
    })

    const contentType = response.headers.get('content-type') || ''
    const payload = contentType.includes('application/json')
      ? await response.json()
      : await response.text()

    if (!response.ok) {
      const detail = typeof payload === 'object' && payload?.detail
        ? payload.detail
        : `HTTP ${response.status}`
      throw new Error(detail)
    }

    return payload
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error('本地中转响应超时，请检查服务是否卡住')
    }
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

export function jsonOptions(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }
}
