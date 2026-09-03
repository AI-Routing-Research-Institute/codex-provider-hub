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
    <div v-else-if="buckets.length && chartName === 'heat'" class="usage-chart usage-chart-heat">
      <svg :viewBox="`0 0 ${heatWidth} ${heatHeight}`" class="usage-chart-svg" role="img" aria-label="Token 消耗热力矩阵，颜色越深消耗越高" preserveAspectRatio="xMidYMid meet">
        <g v-if="granularity === 'hour'">
          <text v-for="label in heatColumnLabels" :key="`c${label.index}`" class="usage-axis-text" :x="heatLeft + label.index * heatCell + heatCell / 2" :y="heatTop - 6" text-anchor="middle">{{ label.text }}</text>
          <text v-for="label in heatRowLabels" :key="`r${label.index}`" class="usage-axis-text" :x="heatLeft - 8" :y="heatTop + label.index * heatCell + heatCell / 2 + 4" text-anchor="end">{{ label.text }}</text>
        </g>
        <g v-else>
          <text v-for="label in heatColumnLabels" :key="`w${label.index}`" class="usage-axis-text" :x="heatLeft + label.index * heatCell + heatCell / 2" :y="heatTop - 6" text-anchor="middle">{{ label.text }}</text>
          <text v-for="label in heatRowLabels" :key="`d${label.index}`" class="usage-axis-text" :x="heatLeft - 8" :y="heatTop + label.index * heatCell + heatCell / 2 + 4" text-anchor="end">{{ label.text }}</text>
        </g>
        <rect v-for="cell in heatCells" :key="cell.key" class="usage-heat-cell" :class="`usage-heat-${cell.level}`" :x="cell.x" :y="cell.y" :width="heatCell - heatGap" :height="heatCell - heatGap" rx="2" @pointerenter="hoverCell = cell" @pointerleave="hoverCell = null" />
      </svg>
      <div v-if="hoverCell && hoverCell.bucket" class="usage-tooltip" :style="heatTooltipStyle">
        <strong>{{ formatBucketTime(hoverCell.bucket.start_at) }}</strong>
        <span>请求 {{ hoverCell.bucket.request_count }}（成功 {{ hoverCell.bucket.successful_requests }} / 失败 {{ hoverCell.bucket.failed_requests }}）</span>
        <span>合计 {{ hoverCell.bucket.total_tokens.toLocaleString() }} Token</span>
      </div>
      <div class="usage-legend">
        <span class="usage-legend-item usage-legend-note">颜色越深 = 该时段消耗越高</span>
        <span class="usage-legend-item"><i class="usage-swatch usage-heat-swatch-1" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-2" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-3" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-4" aria-hidden="true" />合计 Token</span>
      </div>
    </div>
    <div v-else-if="buckets.length" class="usage-chart" @pointermove="trackHover" @pointerleave="hoverIndex = -1">
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="usage-chart-svg" role="img" aria-label="Token 消耗随时间变化曲线" preserveAspectRatio="xMidYMid meet">
        <g v-for="grid in gridLines" :key="grid.value">
          <line class="usage-grid-line" :x1="padLeft" :x2="chartWidth - padRight" :y1="y(grid.value)" :y2="y(grid.value)" />
          <text class="usage-axis-text" :x="padLeft - 8" :y="y(grid.value) + 4" text-anchor="end">{{ grid.label }}</text>
        </g>
        <template v-if="chartName === 'area'">
          <polygon class="usage-area-input" :points="areaPoints(bucket => bucket.input_tokens)" />
          <polygon class="usage-area-output" :points="areaPoints(bucket => bucket.input_tokens + bucket.output_tokens)" />
          <polyline class="usage-line-total" :points="linePoints(bucket => bucket.total_tokens)" />
        </template>
        <template v-else-if="chartName === 'line'">
          <line class="usage-mean-line" :x1="padLeft" :x2="chartWidth - padRight" :y1="y(meanValue)" :y2="y(meanValue)" />
          <polyline class="usage-line-total" :points="linePoints(bucket => bucket.total_tokens)" />
        </template>
        <template v-else>
          <g v-for="(bar, index) in barRects" :key="index">
            <rect class="usage-bar-input" :x="bar.x" :y="bar.inputY" :width="bar.width" :height="bar.inputHeight" />
            <rect class="usage-bar-output" :x="bar.x" :y="bar.outputY" :width="bar.width" :height="bar.outputHeight" />
          </g>
        </template>
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
        <template v-if="chartName === 'area'">
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-input" aria-hidden="true" />输入</span>
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-output" aria-hidden="true" />输出（叠加）</span>
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />合计</span>
        </template>
        <template v-else-if="chartName === 'line'">
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />合计 Token</span>
          <span class="usage-legend-item usage-legend-note">虚线 = 窗口均值 {{ formatTokenCount(meanValue) }}；y 轴从 0 开始</span>
        </template>
        <template v-else>
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-input" aria-hidden="true" />输入</span>
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-output" aria-hidden="true" />输出（堆叠）</span>
          <span class="usage-legend-item usage-legend-note">零基线 · 不断轴</span>
        </template>
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
const chartName = ref(localStorage.getItem('local-proxy-usage-trend-chart') || 'area')
const customRange = ref(null)
const loading = ref(false)
const error = ref('')
const hoverIndex = ref(-1)
const hoverCell = ref(null)
let timer
let reloadAfterCurrent = false
const windowOptions = [
  { value: 'today', label: '今天' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'all', label: '全部' }
]
const chartOptions = [
  { value: 'area', label: '面积', hint: '输入/输出堆叠面积 + 合计线' },
  { value: 'line', label: '折线', hint: '合计折线 + 窗口均值参考' },
  { value: 'bars', label: '柱状', hint: '按桶堆叠柱状（零基线）' },
  { value: 'heat', label: '热力', hint: '时段消耗热力矩阵' }
]
const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const heatCell = 22
const heatGap = 3
const heatLeft = 44
const heatTop = 24

const windowLabel = computed(() => windowOptions.find(option => option.value === windowName.value)?.label || (windowName.value === 'custom' ? '自定义时间' : ''))
const headingMeta = computed(() => {
  if (error.value) return '用量趋势暂不可用'
  const unit = granularity.value === 'hour' ? '按小时' : '按天'
  return buckets.value.length ? `${windowLabel.value} · ${unit}分桶 · ${buckets.value.length} 个数据点` : windowLabel.value ? `${windowLabel.value} · ${unit}分桶` : ''
})
const conclusionLabel = computed(() => `${windowLabel.value || '所选范围'} 合计`)
const maxValue = computed(() => Math.max(1, ...buckets.value.map(bucket => Math.max(Number(bucket.total_tokens || 0), Number(bucket.input_tokens || 0) + Number(bucket.output_tokens || 0)))))
const meanValue = computed(() => {
  if (!buckets.value.length) return 0
  return Math.round(buckets.value.reduce((sum, bucket) => sum + Number(bucket.total_tokens || 0), 0) / buckets.value.length)
})
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
const barRects = computed(() => {
  const count = buckets.value.length
  if (!count) return []
  const innerWidth = chartWidth - padLeft - padRight
  const slot = innerWidth / count
  const width = Math.max(2, slot * 0.68)
  const base = chartHeight - padBottom
  return buckets.value.map((bucket, index) => {
    const input = Number(bucket.input_tokens || 0)
    const output = Number(bucket.output_tokens || 0)
    const inputHeight = Math.max(0, base - y(input))
    const outputHeight = Math.max(0, y(input) - y(input + output))
    return {
      x: padLeft + slot * index + (slot - width) / 2,
      width,
      inputY: base - inputHeight,
      inputHeight,
      outputY: base - inputHeight - outputHeight,
      outputHeight
    }
  })
})
const heatLayout = computed(() => {
  if (granularity.value === 'hour') {
    const rows = new Map()
    for (const bucket of buckets.value) {
      const date = new Date(bucket.start_at)
      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
      if (!rows.has(key)) rows.set(key, { key, label: `${date.getMonth() + 1}月${date.getDate()}日`, cells: new Map() })
      rows.get(key).cells.set(date.getHours(), bucket)
    }
    const rowList = [...rows.values()].sort((a, b) => String(a.key).localeCompare(String(b.key)))
    return { rows: rowList, columns: 24, rowLabelOf: row => row.label, columnIndexOf: bucket => new Date(bucket.start_at).getHours() }
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
  let weeks = 1
  for (const row of rows) for (const key of row.cells.keys()) weeks = Math.max(weeks, key + 1)
  return { rows, columns: weeks, rowLabelOf: row => row.label, columnIndexOf: null, firstMonday }
})
const heatCells = computed(() => {
  const layout = heatLayout.value
  const cells = []
  layout.rows.forEach((row, rowIndex) => {
    for (const [columnIndex, entry] of row.cells) {
      const bucket = entry && entry.bucket ? entry.bucket : entry
      cells.push({
        key: `${row.key}-${columnIndex}`,
        x: heatLeft + columnIndex * heatCell,
        y: heatTop + rowIndex * heatCell,
        level: heatLevel(Number(bucket.total_tokens || 0)),
        bucket
      })
    }
  })
  return cells
})
const heatColumnLabels = computed(() => {
  const layout = heatLayout.value
  const labels = []
  if (granularity.value === 'hour') {
    for (let hour = 0; hour < layout.columns; hour += 3) labels.push({ index: hour, text: String(hour).padStart(2, '0') })
  } else {
    const step = Math.max(1, Math.ceil(layout.columns / 6))
    for (const row of layout.rows) {
      for (const [weekIndex, entry] of row.cells) {
        if (weekIndex % step === 0 && !labels.some(label => label.index === weekIndex)) {
          labels.push({ index: weekIndex, text: entry.label || '' })
        }
      }
    }
  }
  return labels
})
const heatRowLabels = computed(() => heatLayout.value.rows.map((row, index) => ({ index, text: heatLayout.value.rowLabelOf(row) })))
const heatWidth = computed(() => heatLeft + heatLayout.value.columns * heatCell + 8)
const heatHeight = computed(() => heatTop + heatLayout.value.rows.length * heatCell + 8)
const heatTooltipStyle = computed(() => {
  if (!hoverCell.value) return {}
  const ratio = (hoverCell.value.x + heatCell / 2) / heatWidth.value
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})
const tooltipStyle = computed(() => {
  if (hoverIndex.value < 0) return {}
  const ratio = x(hoverIndex.value) / chartWidth
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})

function heatLevel(value) {
  if (value <= 0) return 0
  const ratio = value / maxValue.value
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
function changeChart(value) {
  chartName.value = value
  hoverIndex.value = -1
  hoverCell.value = null
  localStorage.setItem('local-proxy-usage-trend-chart', value)
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
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
    if (reloadAfterCurrent) { reloadAfterCurrent = false; void loadTimeline() }
  }
}
onMounted(() => { loadTimeline(); timer = window.setInterval(loadTimeline, 30000) })
onBeforeUnmount(() => { window.clearInterval(timer); hoverIndex.value = -1; hoverCell.value = null })
</script>
