export function alignMonitorProviders(configuredProviders, statusProviders) {
  const configured = Array.isArray(configuredProviders) ? configuredProviders : []
  const statuses = Array.isArray(statusProviders) ? statusProviders : []
  const configuredById = new Map(configured.map(provider => [providerId(provider), provider]).filter(([id]) => id))
  const statusById = new Map(statuses.map(provider => [providerId(provider), provider]).filter(([id]) => id))
  const ordered = []

  for (const status of statuses) {
    const id = providerId(status)
    const provider = configuredById.get(id)
    if (!provider) continue
    ordered.push({
      ...provider,
      id: provider.id || id,
      state: status.state || provider.state,
      health_status: status,
    })
  }
  for (const provider of configured) {
    if (!statusById.has(providerId(provider))) ordered.push(provider)
  }
  return ordered
}

export function statusUrlForWindow(statusUrl, windowName = '24h') {
  if (!statusUrl) return null
  try {
    const base = typeof window !== 'undefined' && window.location?.origin
      ? window.location.origin
      : 'http://localhost'
    const url = new URL(statusUrl, base)
    url.searchParams.set('window', windowName)
    return url.toString()
  } catch {
    return null
  }
}

function providerId(provider) {
  return String(provider?.id || provider?.provider_id || '').trim()
}
