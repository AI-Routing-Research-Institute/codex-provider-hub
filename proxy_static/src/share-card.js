const WEEKDAY_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function toCount(value) {
  const count = Number(value || 0)
  return Number.isFinite(count) && count > 0 ? count : 0
}

export function formatGroupedTokens(value) {
  const count = toCount(value)
  const digits = String(Math.floor(count))
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

export function shareCardDateLabels(now) {
  const date = now instanceof Date ? now : new Date()
  const timestamp = date.getTime()
  if (!Number.isFinite(timestamp)) return { dateLabel: '', weekdayLabel: '' }
  const year = String(date.getFullYear()).padStart(4, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return {
    dateLabel: `${year}/${month}/${day}`,
    weekdayLabel: WEEKDAY_LABELS[date.getDay()] || ''
  }
}

export function buildShareCardData({ status, now, providerLimit = 4 }) {
  const payload = status && typeof status === 'object' ? status : {}
  const usage = payload.usage && typeof payload.usage === 'object' ? payload.usage : {}
  const total = usage.total && typeof usage.total === 'object' ? usage.total : {}
  const providerList = Array.isArray(payload.providers) ? payload.providers : []
  const providerNames = new Map(
    providerList
      .filter(provider => provider && provider.provider_id)
      .map(provider => [String(provider.provider_id), String(provider.name || provider.provider_id)])
  )
  const totalTokens = toCount(total.total_tokens)
  const requestCount = toCount(total.request_count)
  const successfulRequests = toCount(total.successful_requests)
  const byProvider = Object.entries(usage.by_provider && typeof usage.by_provider === 'object' ? usage.by_provider : {})
    .map(([providerId, summary]) => ({
      providerId,
      name: providerNames.get(String(providerId)) || String(providerId),
      totalTokens: toCount(summary?.total_tokens),
      requestCount: toCount(summary?.request_count)
    }))
    .filter(entry => entry.totalTokens > 0 || entry.requestCount > 0)
    .sort((left, right) => right.totalTokens - left.totalTokens || right.requestCount - left.requestCount)
    .slice(0, Math.max(1, providerLimit))
    .map(entry => ({
      ...entry,
      tokenShare: totalTokens > 0 ? Math.round((entry.totalTokens / totalTokens) * 1000) / 10 : 0
    }))
  const { dateLabel, weekdayLabel } = shareCardDateLabels(now)
  return {
    dateLabel,
    weekdayLabel,
    serviceName: String(payload.service || '本地中转'),
    brandMark: String(payload.brand_mark || 'CX'),
    displayName: String(payload.display_name || '本地中转'),
    totalTokens,
    inputTokens: toCount(total.input_tokens),
    outputTokens: toCount(total.output_tokens),
    cachedTokens: toCount(total.cached_tokens),
    reasoningTokens: toCount(total.reasoning_tokens),
    requestCount,
    successfulRequests,
    failedRequests: Math.max(0, requestCount - successfulRequests),
    successRate: requestCount > 0 ? Math.round((successfulRequests / requestCount) * 1000) / 10 : 0,
    estimatedRequests: toCount(total.estimated_requests),
    byProvider,
    hasData: totalTokens > 0 || requestCount > 0
  }
}

export function shareCardFileName(now) {
  const { dateLabel } = shareCardDateLabels(now)
  const compact = dateLabel ? dateLabel.replace(/\//g, '') : 'unknown'
  return `token-card-${compact}.png`
}
