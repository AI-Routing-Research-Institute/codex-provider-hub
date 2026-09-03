<template>
  <section id="usage-view" class="usage-view view-panel" aria-labelledby="usage-title">
    <div class="usage-heading">
      <div><h2 id="usage-title">用量趋势</h2><p>{{ headingMeta }}</p></div>
      <div class="usage-actions">
        <TimeRangeSelect v-model="windowName" class="usage-window-control" aria-label="用量时间范围" title="用量时间范围" :options="windowOptions" :initial-range="customRange" @change="changeWindow" @apply="applyCustomRange" />
        <button class="icon-button" type="button" title="刷新用量趋势" aria-label="刷新用量趋势" @click="loadTimeline"><UiIcon name="refresh" /></button>
      </div>
    </div>
    <div class="usage-summary">
      <div class="usage-summary-item"><span>合计 Token</span><strong>{{ formatTokenCount(total.total_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>输入 / 输出</span><strong>{{ formatTokenCount(total.input_tokens) }} / {{ formatTokenCount(total.output_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>请求数</span><strong>{{ Number(total.request_count || 0) }}</strong></div>
      <div class="usage-summary-item"><span>成功 / 失败</span><strong>{{ Number(total.successful_requests || 0) }} / {{ Number(total.failed_requests || 0) }}</strong></div>
    </div>
    <div v-if="error" class="usage-error" role="alert">{{ error }}</div>
    <div v-else-if="buckets.length" class="usage-chart" @pointermove="trackHover" @pointerleave="hoverIndex = -1">
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="usage-chart-svg" role="img" aria-label="Token 消耗随时间变化曲线" preserveAspectRatio="xMidYMid meet">
        <g v-for="grid in gridLines" :key="grid.value">
          <line class="usage-grid-line" :x1="padLeft" :x2="chartWidth - padRight" :y1="y(grid.value)" :y2="y(grid.value)" />
          <text class="usage-axis-text" :x="padLeft - 8" :y="y(grid.value) + 4" text-anchor="end">{{ grid.label }}</text>
        </g>
        <polygon class="usage-area-input" :points="areaPoints(bucket => bucket.input_tokens)" />
        <polygon class="usage-area-output" :points="areaPoints(bucket => bucket.input_tokens + bucket.output_tokens)" />
        <polyline class="usage-line-total" :points="linePoints(bucket => bucket.total_tokens)" />
        <g v-for="label in xLabels" :key="label.index">
          <text class="usage-axis-text" :x="x(label.index)" :y="chartHeight - 8" :text-anchor="label.index === 0 ? 'start' : label.index === buckets.length - 1 ? 'end' : 'middle'">{{ label.text }}</text>
        </g>
        <line v-if="hoverIndex >= 0" class="usage-hover-line" :x1="x(hoverIndex)" :x2="x(hoverIndex)" :y1="padTop" :y2="chartHeight - padBottom" />
        <circle v-if="hoverIndex >= 0" class="usage-hover-dot" :cx="x(hoverIndex)" :cy="y(buckets[hoverIndex].total_tokens)" r="4" />
      </svg>
      <div v-if="hoverIndex >= 0 && buckets[hoverIndex]" class="usage-tooltip" :style="tooltipStyle">
        <strong>{{ formatBucketTime(buckets[hoverIndex].start_at) }}</strong>
        <span>请求 {{ buckets[hoverIndex].request_count }}（成功 {{ buckets[hoverIndex].successful_requests }} / 失败 {{ buckets[hoverIndex].failed_requests }}）</span>
        <span>输入 {{ buckets[hoverIndex].input_tokens.toLocaleString() }} · 输出 {{ buckets[hoverIndex].output_tokens.toLocaleString() }}</span>
        <span>合计 {{ buckets[hoverIndex].total_tokens.toLocaleString() }} Token</span>
      </div>
      <div class="usage-legend">
        <span class="usage-legend-item"><i class="usage-swatch usage-swatch-input" aria-hidden="true" />输入</span>
        <span class="usage-legend-item"><i class="usage-swatch usage-swatch-output" aria-hidden="true" />输出（叠加）</span>
        <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />合计</span>
      </div>
    </div>
    <div v-else class="usage-empty">{{ loading ? '正在读取用量趋势…' : '所选时间范围内没有 Token 记录' }}</div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch } from '../api.js'
import TimeRangeSelect from './TimeRangeSelect.vue'
import UiIcon from './ui/UiIcon.vue'
import { formatTokenCount } from '../token-format.js'

const chartWidth = 720
const chartHeight = 260
const padLeft = 56
const padRight = 16
const padTop = 16
const padBottom = 28

const buckets = ref([])
const total = ref({})
const granularity = ref('day')
const windowName = ref(localStorage.getItem('local-proxy-usage-trend-window') || '24h')
const customRange = ref(null)
const loading = ref(false)
const error = ref('')
const hoverIndex = ref(-1)
let timer
let reloadAfterCurrent = false
const windowOptions = [
  { value: 'today', label: '今天' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'all', label: '全部' }
]

const headingMeta = computed(() => {
  if (error.value) return '用量趋势暂不可用'
  const unit = granularity.value === 'hour' ? '按小时' : '按天'
  const label = windowOptions.find(option => option.value === windowName.value)?.label || (windowName.value === 'custom' ? '自定义时间' : '')
  return buckets.value.length ? `${label} · ${unit}分桶 · ${buckets.value.length} 个数据点` : label ? `${label} · ${unit}分桶` : ''
})
const maxValue = computed(() => Math.max(1, ...buckets.value.map(bucket => Math.max(Number(bucket.total_tokens || 0), Number(bucket.input_tokens || 0) + Number(bucket.output_tokens || 0)))))
const gridLines = computed(() => [0, 0.25, 0.5, 0.75, 1].map(ratio => ({ value: Math.round(maxValue.value * ratio), label: formatTokenCount(Math.round(maxValue.value * ratio)) })))
const xLabels = computed(() => {
  const count = buckets.value.length
  if (!count) return []
  const step = Math.max(1, Math.ceil(count / 6))
  const indexes = []
  for (let index = 0; index < count; index += step) indexes.push(index)
  if (indexes[indexes.length - 1] !== count - 1) indexes.push(count - 1)
  return indexes.map(index => ({ index, text: formatBucketLabel(buckets.value[index].start_at) }))
})
const tooltipStyle = computed(() => {
  if (hoverIndex.value < 0) return {}
  const ratio = x(hoverIndex.value) / chartWidth
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})

function x(index) {
  const count = buckets.value.length
  if (count <= 1) return padLeft
  return padLeft + (index / (count - 1)) * (chartWidth - padLeft - padRight)
}
function y(value) { return padTop + (chartHeight - padTop - padBottom) * (1 - Math.min(value, maxValue.value) / maxValue.value) }
function linePoints(valueOf) { return buckets.value.map((bucket, index) => `${x(index)},${y(valueOf(bucket))}`).join(' ') }
function areaPoints(valueOf) {
  const base = chartHeight - padBottom
  const head = buckets.value.map((bucket, index) => `${x(index)},${y(valueOf(bucket))}`).join(' ')
  const tail = buckets.value.map((bucket, index) => `${x(buckets.value.length - 1 - index)},${base}`).join(' ')
  return `${head} ${tail}`
}
function formatBucketLabel(value) {
  const date = new Date(value)
  return granularity.value === 'hour' ? `${String(date.getHours()).padStart(2, '0')}:00` : `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function formatBucketTime(value) {
  const date = new Date(value)
  const dateText = `${date.getMonth() + 1}月${date.getDate()}日`
  return granularity.value === 'hour' ? `${dateText} ${String(date.getHours()).padStart(2, '0')}:00` : dateText
}
function trackHover(event) {
  const bounds = event.currentTarget.getBoundingClientRect()
  const plotRatio = (event.clientX - bounds.left) / bounds.width
  const plotX = plotRatio * chartWidth - padLeft
  const innerWidth = chartWidth - padLeft - padRight
  const count = buckets.value.length
  if (count <= 1) { hoverIndex.value = plotX >= 0 && plotX <= innerWidth ? 0 : -1; return }
  const ratio = Math.min(1, Math.max(0, plotX / innerWidth))
  hoverIndex.value = Math.round(ratio * (count - 1))
}
function changeWindow(value) {
  if (value !== 'custom') customRange.value = null
  localStorage.setItem('local-proxy-usage-trend-window', value)
  loadTimeline()
}
function applyCustomRange(range) {
  customRange.value = range
  windowName.value = 'custom'
  localStorage.setItem('local-proxy-usage-trend-window', 'custom')
  loadTimeline()
}
function timelineParams() {
  const params = new URLSearchParams({ usage_window: windowName.value })
  if (windowName.value === 'custom' && customRange.value) {
    params.set('start_at', String(customRange.value.startAt))
    params.set('end_at', String(customRange.value.endAt))
  }
  return params
}
async function loadTimeline() {
  if (loading.value) { reloadAfterCurrent = true; return }
  if (windowName.value === 'custom' && !customRange.value) return
  loading.value = true; error.value = ''
  try {
    const payload = await controlFetch(`/api/usage-timeline?${timelineParams()}`)
    buckets.value = Array.isArray(payload.buckets) ? payload.buckets : []
    total.value = payload.total || {}
    granularity.value = payload.granularity === 'hour' ? 'hour' : 'day'
    hoverIndex.value = -1
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
    if (reloadAfterCurrent) { reloadAfterCurrent = false; void loadTimeline() }
  }
}
onMounted(() => { loadTimeline(); timer = window.setInterval(loadTimeline, 30000) })
onBeforeUnmount(() => { window.clearInterval(timer); hoverIndex.value = -1 })
</script>
