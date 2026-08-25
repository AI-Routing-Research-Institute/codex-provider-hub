export function dayRange(startDate, endDate) {
  const startParts = String(startDate || '').split('-').map(Number)
  const endParts = String(endDate || '').split('-').map(Number)
  if (startParts.length !== 3 || endParts.length !== 3 || [...startParts, ...endParts].some(value => !Number.isInteger(value))) return null
  const startAt = new Date(startParts[0], startParts[1] - 1, startParts[2]).getTime()
  const endAt = new Date(endParts[0], endParts[1] - 1, endParts[2] + 1).getTime()
  if (!Number.isFinite(startAt) || !Number.isFinite(endAt)) return null
  return { startAt, endAt }
}

export function preciseRange(startDate, startTime, endDate, endTime) {
  const start = `${startDate}T${startTime || '00:00:00'}`
  const end = `${endDate}T${endTime || '00:00:00'}`
  const startAt = new Date(start).getTime()
  const endAt = new Date(end).getTime()
  if (!Number.isFinite(startAt) || !Number.isFinite(endAt)) return null
  return { startAt, endAt }
}
