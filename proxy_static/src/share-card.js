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

const BADGE_TIERS = [
  { minTokens: 10_000_000, title: 'Token 吞天巨兽' },
  { minTokens: 1_000_000, title: 'Token 大户' },
  { minTokens: 100_000, title: 'Token 渐入佳境' },
  { minTokens: 10_000, title: 'Token 操练生' },
  { minTokens: 1, title: 'Token 初出茅庐' }
]

export function shareBadgeTitle(totalTokens) {
  const count = toCount(totalTokens)
  return BADGE_TIERS.find(tier => count >= tier.minTokens)?.title || '今日蓄力中'
}

export function yesterdayCompare(todayTokens, yesterdayTokens) {
  const today = toCount(todayTokens)
  const yesterday = toCount(yesterdayTokens)
  if (yesterday <= 0) {
    return today > 0
      ? { direction: 'up', percent: 0, label: '首日作战 · 明日开启对比' }
      : { direction: null, percent: 0, label: '' }
  }
  const percent = Math.round(((today - yesterday) / yesterday) * 1000) / 10
  if (percent === 0) return { direction: 'flat', percent: 0, label: '与昨日持平' }
  if (percent > 0) return { direction: 'up', percent, label: `较昨日多 ${Math.abs(percent)}%` }
  return { direction: 'down', percent, label: `较昨日少 ${Math.abs(percent)}%` }
}

export function latestBattleLabel(lastRequestAt) {
  const number = Number(lastRequestAt)
  if (!Number.isFinite(number) || number <= 0) return ''
  const date = new Date(number < 10_000_000_000 ? number * 1000 : number)
  if (Number.isNaN(date.getTime())) return ''
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${hour}:${minute}`
}

export function posterStars({ count, width, height, seed = 20260901 }) {
  const points = []
  let state = Math.floor(Math.abs(seed)) % 2147483647 || 1
  const next = () => {
    state = (state * 48271) % 2147483647
    return state / 2147483647
  }
  for (let index = 0; index < count; index += 1) {
    points.push({
      x: Math.round(next() * width),
      y: Math.round(next() * height),
      radius: 0.8 + next() * 1.8,
      alpha: 0.25 + next() * 0.65,
      sparkle: index % 7 === 0
    })
  }
  return points
}
