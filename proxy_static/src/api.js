const pathMatch = window.location.pathname.match(/^\/control\/(codex|claude)(?:\/|$)/)

export const controlBase = pathMatch ? `/control/${pathMatch[1]}` : '/control/codex'

export function controlUrl(path) {
  if (/^\/control\/(codex|claude)(?:\/|$)/.test(path)) return path
  return `${controlBase}${path.startsWith('/') ? path : `/${path}`}`
}

export async function controlFetch(path, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('X-Local-Proxy-Control', '1')

  const response = await fetch(controlUrl(path), {
    cache: 'no-store',
    ...options,
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
}

export function jsonOptions(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }
}
