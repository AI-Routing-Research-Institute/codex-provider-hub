<template>
  <section id="usage-view" class="usage-view view-panel" aria-labelledby="usage-title">
    <div class="usage-heading">
      <div><h2 id="usage-title">用量趋势</h2><p>{{ headingMeta }}</p></div>
      <div class="usage-actions">
        <div class="usage-chart-toggle" role="group" aria-label="图表类型">
          <button v-for="chart in chartOptions" :key="chart.value" type="button" class="usage-chart-toggle-button" :class="{ 'is-active': chartName === chart.value }" :title="chart.hint" @click="changeChart(chart.value)">{{ chart.label }}</button>
        </div>
        <TimeRangeSelect v-model="windowName" class="usage-window-control" aria-label="用量时间范围" title="用量时间范围" :options="windowOptions" :initial-range="customRange" @change="changeWindow" @apply="applyCustomRange" />
        <button class="icon-button" type="button" title="刷新用量趋势" aria-label="刷新用量趋势" @click="loadTimeline"><UiIcon name="refresh" /></button>
      </div>
    </div>
    <div class="usage-summary">
      <div class="usage-summary-item usage-conclusion"><span>{{ conclusionLabel }}</span><strong>{{ formatTokenCount(total.total_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>输入 / 输出</span><strong>{{ formatTokenCount(total.input_tokens) }} / {{ formatTokenCount(total.output_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>请求数</span><strong>{{ Number(total.request_count || 0) }}</strong></div>
      <div class="usage-summary-item"><span>成功 / 失败</span><strong>{{ Number(total.successful_requests || 0) }} / {{ Number(total.failed_requests || 0) }}</strong></div>
    </div>
    <div v-if="error" class="usage-error" role="alert">{{ error }}</div>
    <div v-else-if="buckets.length" class="usage-chart-stage" @pointerenter="scheduleHoverReplay">
      <div v-if="chartName !== 'heat'" class="usage-metric-pills" role="group" aria-label="指标维度">
        <button v-for="(metric, index) in metrics" :key="metric.id" type="button" class="usage-metric-pill" :class="{ 'is-active': metricIndex === index }" @click="changeMetric(index)">{{ metric.label }}</button>
        <span class="usage-metric-auto" :title="carouselling ? '每 5 秒自动切换指标，悬停图表时暂停' : '已跟随系统减少动态效果，指标不自动切换'">{{ carouselling ? 'auto ↻' : '' }}</span>
      </div>

      <div v-if="chartName === 'heat'" class="usage-chart usage-chart-heat">
        <svg :width="heatGeom.width" :height="heatGeom.height" class="usage-heat-svg" role="img" aria-label="Token 消耗热力矩阵，颜色越深消耗越高">
          <text v-for="label in heatGeom.colLabels" :key="`c${label.index}`" class="usage-axis-text" :x="heatLeft + label.index * heatGeom.cell + heatGeom.cell / 2" :y="heatTop - 5" text-anchor="middle">{{ label.text }}</text>
          <text v-for="label in heatGeom.rowLabels" :key="`r${label.index}`" class="usage-axis-text" :x="heatLeft - 6" :y="heatTop + label.index * heatGeom.cell + heatGeom.cell / 2 + 4" text-anchor="end">{{ label.text }}</text>
          <g :key="`cells-${animKey}`">
            <rect v-for="(cell, index) in heatGeom.cells" :key="cell.key" class="usage-heat-cell" :class="[`usage-heat-${cell.level}`, { 'is-animated': !reducedMotion }]" :style="{ animationDelay: `${Math.min(index * 12, 600)}ms` }" :x="cell.x" :y="cell.y" :width="heatGeom.cell - 2" :height="heatGeom.cell - 2" rx="2" @pointerenter="hoverCell = cell" @pointerleave="hoverCell = null" />
          </g>
        </svg>
        <div v-if="hoverCell && hoverCell.bucket" class="usage-tooltip usage-tooltip-heat" :style="heatTooltipStyle">
          <strong>{{ formatBucketTime(hoverCell.bucket.start_at) }}</strong>
          <span>请求 {{ hoverCell.bucket.request_count }}（成功 {{ hoverCell.bucket.successful_requests }} / 失败 {{ hoverCell.bucket.failed_requests }}）</span>
          <span>合计 {{ hoverCell.bucket.total_tokens.toLocaleString() }} Token</span>
        </div>
        <div class="usage-legend usage-legend-inline">
          <span class="usage-legend-item usage-legend-note">颜色越深 = 该时段消耗越高</span>
          <span class="usage-legend-item"><i class="usage-swatch usage-heat-swatch-1" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-2" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-3" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-4" aria-hidden="true" />合计 Token</span>
        </div>
      </div>

      <div v-else class="usage-chart" @pointermove="trackHover" @pointerleave="hoverIndex = -1">
        <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="usage-chart-svg" role="img" :aria-label="chartName === 'cumulative' ? `累计${metric.label}曲线` : `${metric.label}随时间变化曲线`" preserveAspectRatio="xMidYMid meet">
          <g v-for="grid in gridLines" :key="grid.value">
            <line class="usage-grid-line" :x1="padLeft" :x2="chartWidth - padRight" :y1="y(grid.value)" :y2="y(grid.value)" />
            <text class="usage-axis-text" :x="padLeft - 8" :y="y(grid.value) + 4" text-anchor="end">{{ grid.label }}</text>
          </g>

          <template v-if="chartName === 'trend'">
            <path v-if="metric.id === 'tokens'" class="usage-area-input" :d="areaPath(bucket => bucket.input_tokens)" :style="{ opacity: drawProgress }" />
            <path v-if="metric.id === 'tokens'" class="usage-area-output" :d="areaPath(bucket => bucket.input_tokens + bucket.output_tokens)" :style="{ opacity: drawProgress }" />
            <path v-if="metric.id === 'requests'" class="usage-area-output" :d="areaPath(bucket => bucket.request_count)" :style="{ opacity: drawProgress }" />
            <path class="usage-line-total" :d="linePath(lineValueOf)" :style="lineDrawStyle" />
          </template>

          <template v-else>
            <path class="usage-area-cumulative" :d="cumulativeAreaPath()" :style="{ opacity: drawProgress * 0.5 }" />
            <path class="usage-line-cumulative" :d="cumulativeLinePath()" :style="lineDrawStyle" />
            <circle v-if="buckets.length" class="usage-hover-dot" :cx="x(buckets.length - 1)" :cy="y(cumulativeFinal)" r="3.5" :style="{ opacity: drawProgress }" />
            <text class="usage-cumulative-number" :x="chartWidth - padRight" y="26" text-anchor="end">{{ counterDisplay }}</text>
            <text class="usage-cumulative-label" :x="chartWidth - padRight" y="42" text-anchor="end">累计{{ metric.id === 'requests' ? '请求' : ' Tokens' }}</text>
          </template>

          <g v-for="label in xLabels" :key="label.index">
            <text class="usage-axis-text" :x="x(label.index)" :y="chartHeight - 6" :text-anchor="label.index === 0 ? 'start' : label.index === buckets.length - 1 ? 'end' : 'middle'">{{ label.text }}</text>
          </g>
          <line v-if="hoverIndex >= 0" class="usage-hover-line" :x1="x(hoverIndex)" :x2="x(hoverIndex)" :y1="padTop" :y2="chartHeight - padBottom" />
          <circle v-if="hoverIndex >= 0" class="usage-hover-dot" :cx="x(hoverIndex)" :cy="y(hoverValue)" r="4" />
        </svg>
        <div v-if="hoverIndex >= 0 && buckets[hoverIndex]" class="usage-tooltip" :style="tooltipStyle">
          <strong>{{ formatBucketTime(buckets[hoverIndex].start_at) }}</strong>
          <span>请求 {{ buckets[hoverIndex].request_count }}（成功 {{ buckets[hoverIndex].successful_requests }} / 失败 {{ buckets[hoverIndex].failed_requests }}）</span>
          <span>输入 {{ buckets[hoverIndex].input_tokens.toLocaleString() }} · 输出 {{ buckets[hoverIndex].output_tokens.toLocaleString() }}</span>
          <span v-if="chartName === 'cumulative'">累计 {{ counterDisplay }}</span>
          <span v-else>合计 {{ buckets[hoverIndex].total_tokens.toLocaleString() }} Token</span>
        </div>
        <div class="usage-legend usage-legend-inline">
          <template v-if="chartName === 'trend' && metric.id === 'tokens'">
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-input" aria-hidden="true" />输入</span>
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-output" aria-hidden="true" />输出（叠加）</span>
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />合计</span>
          </template>
          <template v-else-if="chartName === 'trend' && metric.id === 'requests'">
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-output" aria-hidden="true" />请求数</span>
          </template>
          <template v-else-if="chartName === 'trend'">
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />成功率</span>
          </template>
          <template v-else>
            <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />累计曲线</span>
          </template>
          <span class="usage-legend-item usage-legend-note">y 轴从 0 开始 · 不断轴</span>
        </div>
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
const chartHeight = 220
const padLeft = 52
const padRight = 14
const padTop = 14
const padBottom = 24
const DRAW_MS = 900
const CAROUSEL_MS = 5000
const HEAT_LEFT = 36
const HEAT_TOP = 20
const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const metrics = [
  { id: 'tokens', label: 'Tokens' },
  { id: 'requests', label: '请求数' },
  { id: 'success', label: '成功率' }
]

const buckets = ref([])
const total = ref({})
const granularity = ref('day')
const windowName = ref(localStorage.getItem('local-proxy-usage-trend-window') || '24h')
const chartName = ref(normalizeChart(localStorage.getItem('local-proxy-usage-trend-chart')))
const customRange = ref(null)
const metricIndex = ref(0)
const loading = ref(false)
const error = ref('')
const hoverIndex = ref(-1)
const hoverCell = ref(null)
const drawProgress = ref(1)
const animKey = ref(0)
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
let timer
let carouselTimer
let rafId = 0
let lastReplayAt = 0
let reloadAfterCurrent = false
const windowOptions = [
  { value: 'today', label: '今天' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'all', label: '全部' }
]
const chartOptions = [
  { value: 'trend', label: '趋势', hint: '时序趋势（面积 + 合计线）' },
  { value: 'heat', label: '热力', hint: '时段消耗热力矩阵' },
  { value: 'cumulative', label: '累计', hint: '累计冲击线 + 大数字' }
]

function normalizeChart(value) {
  if (value === 'heat' || value === 'cumulative') return value
  return 'trend'
}

function quarticOut(t) { return 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 4) }

const metric = computed(() => metrics[metricIndex.value] || metrics[0])
const carouselling = computed(() => !reducedMotion && chartName.value !== 'heat')
const windowLabel = computed(() => windowOptions.find(option => option.value === windowName.value)?.label || (windowName.value === 'custom' ? '自定义时间' : ''))
const headingMeta = computed(() => {
  if (error.value) return '用量趋势暂不可用'
  const unit = granularity.value === 'hour' ? '按小时' : '按天'
  return buckets.value.length ? `${windowLabel.value} · ${unit}分桶 · ${buckets.value.length} 个数据点` : windowLabel.value ? `${windowLabel.value} · ${unit}分桶` : ''
})
const conclusionLabel = computed(() => `${windowLabel.value || '所选范围'} 合计`)

const cumulativeBuckets = computed(() => {
  let sum = 0
  return buckets.value.map(bucket => {
    sum += Number(bucket.total_tokens || 0)
    return sum
  })
})
const cumulativeRequestBuckets = computed(() => {
  let sum = 0
  return buckets.value.map(bucket => {
    sum += Number(bucket.request_count || 0)
    return sum
  })
})
const cumulativeValues = computed(() => (metric.value.id === 'requests' ? cumulativeRequestBuckets.value : cumulativeBuckets.value))
const cumulativeFinal = computed(() => cumulativeValues.value[cumulativeValues.value.length - 1] || 0)
const counterDisplay = computed(() => {
  const shown = Math.round(Number(drawProgress.value) * Number(cumulativeFinal.value || 0))
  return metric.value.id === 'requests' ? shown.toLocaleString() : formatTokenCount(shown)
})

const maxValue = computed(() => {
  if (chartName.value === 'cumulative') return Math.max(1, cumulativeFinal.value)
  if (metric.value.id === 'success') {
    return Math.max(1, ...buckets.value.map(bucket => successRateOf(bucket)))
  }
  if (metric.value.id === 'requests') {
    return Math.max(1, ...buckets.value.map(bucket => Number(bucket.request_count || 0)))
  }
  return Math.max(1, ...buckets.value.map(bucket => Math.max(Number(bucket.total_tokens || 0), Number(bucket.input_tokens || 0) + Number(bucket.output_tokens || 0))))
})
const gridLines = computed(() => {
  const unitSuffix = metric.value.id === 'success' ? '%' : ''
  return [0, 0.25, 0.5, 0.75, 1].map(ratio => {
    const value = Math.round(maxValue.value * ratio)
    return { value, label: `${formatTokenCount(value)}${unitSuffix}` }
  })
})
const xLabels = computed(() => {
  const count = buckets.value.length
  if (!count) return []
  const step = Math.max(1, Math.ceil(count / 6))
  const indexes = []
  for (let index = 0; index < count; index += step) indexes.push(index)
  if (indexes[indexes.length - 1] !== count - 1) indexes.push(count - 1)
  return indexes.map(index => ({ index, text: formatBucketLabel(buckets.value[index].start_at) }))
})
const hoverValue = computed(() => {
  const bucket = buckets.value[hoverIndex.value]
  if (!bucket) return 0
  if (chartName.value === 'cumulative') return cumulativeValues.value[hoverIndex.value] || 0
  if (metric.value.id === 'success') return successRateOf(bucket)
  if (metric.value.id === 'requests') return Number(bucket.request_count || 0)
  return Number(bucket.total_tokens || 0)
})

const heatGeom = computed(() => buildHeatGeom())
const heatTooltipStyle = computed(() => {
  if (!hoverCell.value) return {}
  const ratio = (hoverCell.value.x + heatGeom.value.cell / 2) / heatGeom.value.width
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})
const tooltipStyle = computed(() => {
  if (hoverIndex.value < 0) return {}
  const ratio = x(hoverIndex.value) / chartWidth
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})
const lineDrawStyle = computed(() => ({ strokeDasharray: pathLength.value, strokeDashoffset: pathLength.value * (1 - drawProgress.value) }))
const pathLength = ref(0)

function successRateOf(bucket) {
  const requests = Number(bucket.request_count || 0)
  if (!requests) return 0
  return Math.round((Number(bucket.successful_requests || 0) / requests) * 1000) / 10
}

function lineValueOf(bucket) {
  if (metric.value.id === 'success') return successRateOf(bucket)
  if (metric.value.id === 'requests') return Number(bucket.request_count || 0)
  return Number(bucket.total_tokens || 0)
}

function buildHeatGeom() {
  if (granularity.value === 'hour') {
    const rowsMap = new Map()
    for (const bucket of buckets.value) {
      const date = new Date(bucket.start_at)
      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
      if (!rowsMap.has(key)) rowsMap.set(key, { key, label: `${date.getMonth() + 1}月${date.getDate()}日`, cells: new Map() })
      rowsMap.get(key).cells.set(date.getHours(), bucket)
    }
    const rows = [...rowsMap.values()].sort((a, b) => String(a.key).localeCompare(String(b.key)))
    return finishHeatGeom(rows, 24, row => row.label, bucket => new Date(bucket.start_at).getHours())
  }
  const rows = WEEKDAY_LABELS.map((label, index) => ({ key: String(index), label, cells: new Map() }))
  let firstMonday = null
  for (const bucket of buckets.value) {
    const date = new Date(bucket.start_at)
    const weekday = (date.getDay() + 6) % 7
    if (firstMonday === null) firstMonday = new Date(date.getFullYear(), date.getMonth(), date.getDate() - weekday)
    const weekIndex = Math.floor((date - firstMonday) / (7 * 24 * 3600 * 1000))
    rows[weekday].cells.set(weekIndex, { bucket, label: `${date.getMonth() + 1}-${date.getDate()}` })
  }
  return finishHeatGeom(rows, null, row => row.label)
}

function finishHeatGeom(rows, fixedColumns, rowLabelOf) {
  let columns = fixedColumns
  if (columns == null) {
    columns = 1
    for (const row of rows) for (const key of row.cells.keys()) columns = Math.max(columns, key + 1)
  }
  const availableWidth = chartWidth - HEAT_LEFT - 8
  const availableHeight = chartHeight - HEAT_TOP - 8
  const cell = Math.max(10, Math.min(26, Math.floor(Math.min(availableWidth / Math.max(1, columns), availableHeight / Math.max(1, rows.length)))))
  const maxToken = Math.max(1, ...buckets.value.map(bucket => Number(bucket.total_tokens || 0)))
  const cells = []
  rows.forEach((row, rowIndex) => {
    for (const [columnIndex, entry] of row.cells) {
      const bucket = entry && entry.bucket ? entry.bucket : entry
      cells.push({
        key: `${row.key}-${columnIndex}`,
        x: HEAT_LEFT + columnIndex * cell,
        y: HEAT_TOP + rowIndex * cell,
        level: heatLevel(Number(bucket.total_tokens || 0), maxToken),
        bucket
      })
    }
  })
  const colLabels = []
  const colStep = Math.max(1, Math.ceil(columns / 8))
  if (fixedColumns === 24) {
    for (let hour = 0; hour < columns; hour += colStep) colLabels.push({ index: hour, text: String(hour).padStart(2, '0') })
  } else {
    const seen = new Set()
    for (const row of rows) {
      for (const [weekIndex, entry] of row.cells) {
        if (weekIndex % colStep === 0 && !seen.has(weekIndex)) {
          seen.add(weekIndex)
          colLabels.push({ index: weekIndex, text: entry.label || '' })
        }
      }
    }
  }
  return {
    cell,
    cells,
    colLabels,
    rowLabels: rows.map((row, index) => ({ index, text: rowLabelOf(row) })),
    width: HEAT_LEFT + columns * cell + 8,
    height: HEAT_TOP + rows.length * cell + 8
  }
}

function heatLevel(value, maxToken) {
  if (value <= 0) return 0
  const ratio = value / maxToken
  if (ratio > 0.75) return 4
  if (ratio > 0.5) return 3
  if (ratio > 0.25) return 2
  return 1
}

function x(index) {
  const count = buckets.value.length
  if (count <= 1) return padLeft
  return padLeft + (index / (count - 1)) * (chartWidth - padLeft - padRight)
}
function y(value) { return padTop + (chartHeight - padTop - padBottom) * (1 - Math.min(value, maxValue.value) / maxValue.value) }
function linePath(valueOf) {
  return `M ${buckets.value.map((bucket, index) => `${x(index).toFixed(1)} ${y(valueOf(bucket)).toFixed(1)}`).join(' L ')}`
}
function cumulativeLinePath() {
  return `M ${cumulativeValues.value.map((value, index) => `${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(' L ')}`
}
function cumulativeAreaPath() {
  const base = chartHeight - padBottom
  const head = cumulativeValues.value.map((value, index) => `${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(' L ')
  const tail = cumulativeValues.value.map((value, index) => `${x(cumulativeValues.value.length - 1 - index).toFixed(1)} ${base}`).join(' L ')
  return `M ${head} L ${tail} Z`
}
function areaPath(valueOf) {
  const base = chartHeight - padBottom
  const head = buckets.value.map((bucket, index) => `${x(index).toFixed(1)} ${y(valueOf(bucket)).toFixed(1)}`).join(' L ')
  const tail = buckets.value.map((bucket, index) => `${x(buckets.value.length - 1 - index).toFixed(1)} ${base}`).join(' L ')
  return `M ${head} L ${tail} Z`
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

function replayEntrance() {
  animKey.value += 1
  if (reducedMotion) { drawProgress.value = 1; pathLength.value = 0; return }
  cancelAnimationFrame(rafId)
  pathLength.value = estimatePathLength()
  const startAt = performance.now()
  const step = now => {
    const progress = Math.min(1, (now - startAt) / DRAW_MS)
    drawProgress.value = quarticOut(progress)
    if (progress < 1) rafId = requestAnimationFrame(step)
  }
  rafId = requestAnimationFrame(step)
  lastReplayAt = performance.now()
}

function estimatePathLength() {
  const count = buckets.value.length
  if (count < 2) return 0
  let length = 0
  let previousX = x(0)
  let previousY = y(chartName.value === 'cumulative' ? cumulativeValues.value[0] : lineValueOf(buckets.value[0]))
  for (let index = 1; index < count; index += 1) {
    const currentX = x(index)
    const currentY = y(chartName.value === 'cumulative' ? cumulativeValues.value[index] : lineValueOf(buckets.value[index]))
    length += Math.hypot(currentX - previousX, currentY - previousY)
    previousX = currentX
    previousY = currentY
  }
  return length
}

function scheduleHoverReplay() {
  if (reducedMotion || document.hidden) return
  if (performance.now() - lastReplayAt < 1500) return
  replayEntrance()
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

function changeChart(value) {
  chartName.value = value
  hoverIndex.value = -1
  hoverCell.value = null
  localStorage.setItem('local-proxy-usage-trend-chart', value)
  replayEntrance()
}
function changeMetric(index) {
  metricIndex.value = index
  replayEntrance()
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
    hoverCell.value = null
    replayEntrance()
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
    if (reloadAfterCurrent) { reloadAfterCurrent = false; void loadTimeline() }
  }
}

onMounted(() => {
  loadTimeline()
  timer = window.setInterval(loadTimeline, 30000)
  carouselTimer = window.setInterval(() => {
    if (!carouselling.value || document.hidden || hoverIndex.value >= 0 || hoverCell.value) return
    metricIndex.value = (metricIndex.value + 1) % metrics.length
  }, CAROUSEL_MS)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  window.clearInterval(carouselTimer)
  cancelAnimationFrame(rafId)
  hoverIndex.value = -1
  hoverCell.value = null
})
</script>
