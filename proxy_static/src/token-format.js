export function formatTokenCount(value) {
  const count = Number(value || 0)
  if (!Number.isFinite(count)) return '0'
  const absoluteCount = Math.abs(count)
  const units = [
    { threshold: 1_000_000_000, suffix: 'B' },
    { threshold: 1_000_000, suffix: 'M' },
    { threshold: 1_000, suffix: 'K' }
  ]
  const unit = units.find(({ threshold }) => absoluteCount >= threshold)
  if (!unit) return String(Math.round(count))
  const compact = Number((count / unit.threshold).toFixed(2))
  return `${compact}${unit.suffix}`
}
