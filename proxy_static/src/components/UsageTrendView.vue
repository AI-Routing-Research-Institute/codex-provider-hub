<template>
  <section id="usage-view" class="usage-view view-panel" aria-labelledby="usage-title">
    <div class="usage-heading">
      <div><h2 id="usage-title">用量趋势</h2><p>{{ headingMeta }}</p></div>
      <div class="usage-actions">
        <div class="usage-chart-toggle" role="group" aria-label="图表类型">
          <button v-for="chart in chartOptions" :key="chart.value" type="button" class="usage-chart-toggle-button" :class="{ 'is-active': chartName === chart.value }" :title="chart.hint" @click="changeChart(chart.value)">{{ chart.label }}</button>
        </div>
        <TimeRangeSelect v-model="windowName" class="usage-window-control" aria-label="用量时间范围" title="用量时间范围" :options="windowOptions" :initial-range="customRange" @change="changeWindow" @apply="applyCustomRange" />
        <button class="icon-button" type="button" title="刷新用量趋势" aria-label="刷新用量趋势" @click="loadAll"><UiIcon name="refresh" /></button>
      </div>
    </div>
    <div class="usage-summary">
      <div class="usage-summary-item usage-conclusion"><span>{{ conclusionLabel }}</span><strong>{{ formatTokenCount(total.total_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>输入 / 输出</span><strong>{{ formatTokenCount(total.input_tokens) }} / {{ formatTokenCount(total.output_tokens) }}</strong></div>
      <div class="usage-summary-item"><span>请求数</span><strong>{{ Number(total.request_count || 0) }}</strong></div>
      <div class="usage-summary-item"><span>成功 / 失败</span><strong>{{ Number(total.successful_requests || 0) }} / {{ Number(total.failed_requests || 0) }}</strong></div>
    </div>
    <div v-if="error" class="usage-error" role="alert">{{ error }}</div>
    <div v-else-if="chartEmpty" class="usage-empty">{{ loading ? '正在读取用量数据…' : emptyText }}</div>
    <div v-else class="usage-chart-stage" @pointerenter="scheduleHoverReplay">
      <div v-if="pillsVisible" class="usage-metric-pills" role="group" aria-label="指标维度">
        <button v-for="(metric, index) in metrics" :key="metric.id" type="button" class="usage-metric-pill" :class="{ 'is-active': metricIndex === index }" @click="changeMetric(index)">{{ metric.label }}</button>
        <span class="usage-metric-auto" :title="carouselling ? '每 5 秒自动切换指标，悬停图表时暂停' : '已跟随系统减少动态效果，指标不自动切换'">{{ carouselling ? 'auto ↻' : '' }}</span>
      </div>

      <div class="usage-chart">
        <svg v-if="chartName === 'calendar' && granularity === 'hour'" :width="heatGeom.width" :height="heatGeom.height" class="usage-heat-svg" role="img" aria-label="Token 消耗小时方格热力图，颜色越深消耗越高">
          <text v-for="label in heatGeom.colLabels" :key="`hc${label.index}`" class="usage-axis-text" :x="heatGeom.left + label.index * heatGeom.cell + heatGeom.cell / 2" :y="heatGeom.top - 6" text-anchor="middle">{{ label.text }}</text>
          <text v-for="label in heatGeom.rowLabels" :key="`hr${label.index}`" class="usage-axis-text" :x="heatGeom.left - 8" :y="heatGeom.top + label.index * heatGeom.cell + heatGeom.cell / 2 + 4" text-anchor="end">{{ label.text }}</text>
          <g :key="`heat-${animKey}`">
            <rect v-for="(cell, index) in heatGeom.cells" :key="cell.key" class="usage-heat-cell" :class="[`usage-heat-${cell.level}`, { 'is-animated': !reducedMotion }]" :style="{ animationDelay: `${Math.min(index * 12, 600)}ms` }" :x="cell.x" :y="cell.y" :width="heatGeom.cell - 2" :height="heatGeom.cell - 2" rx="2" @pointerenter="hoverCell = cell" @pointerleave="hoverCell = null" />
          </g>
        </svg>
        <svg v-else-if="chartName === 'calendar'" :width="calendarGeom.width" :height="calendarGeom.height" class="usage-heat-svg" role="img" aria-label="GitHub 风格日历热力图，圆点越大颜色越深表示当日消耗越高">
          <text v-for="label in calendarGeom.colLabels" :key="`mc${label.index}`" class="usage-axis-text" :x="calendarGeom.left + label.index * calendarGeom.cell + calendarGeom.cell / 2" :y="calendarGeom.top - 6" text-anchor="middle">{{ label.text }}</text>
          <text v-for="label in calendarGeom.rowLabels" :key="`mr${label.index}`" class="usage-axis-text" :x="calendarGeom.left - 8" :y="calendarGeom.top + label.index * calendarGeom.cell + calendarGeom.cell / 2 + 4" text-anchor="end">{{ label.text }}</text>
          <g :key="`cal-${animKey}`">
            <circle v-for="(dot, index) in calendarGeom.dots" :key="dot.key" class="usage-cal-dot" :class="`usage-heat-${dot.level}`" :style="{ animationDelay: `${Math.min(index * 6, 600)}ms` }" :cx="dot.cx" :cy="dot.cy" :r="dot.radius" @pointerenter="hoverCell = dot" @pointerleave="hoverCell = null" />
            <circle v-if="calendarGeom.peak" class="usage-cal-peak-ring" :cx="calendarGeom.peak.cx" :cy="calendarGeom.peak.cy" :r="calendarGeom.peak.radius + 4" />
          </g>
        </svg>
        <div v-else-if="chartName === 'punch'" class="usage-punch-wrap">
          <div v-if="punchEmpty" class="usage-empty usage-empty-inline">{{ loading ? '正在读取时段节律…' : '所选时间范围内没有 Token 记录' }}</div>
          <svg v-else :width="punchGeom.width" :height="punchGeom.height" class="usage-heat-svg" role="img" aria-label="星期乘以24小时的消耗节律点阵，圆点越大表示该时段消耗越高">
            <text v-for="label in punchGeom.colLabels" :key="`pc${label}`" class="usage-axis-text" :x="punchGeom.left + label * punchGeom.cell + punchGeom.cell / 2" :y="punchGeom.top - 6" text-anchor="middle">{{ label }}</text>
            <text v-for="label in punchGeom.rowLabels" :key="`pr${label.index}`" class="usage-axis-text" :x="punchGeom.left - 8" :y="punchGeom.top + label.index * punchGeom.cell + punchGeom.cell / 2 + 4" text-anchor="end">{{ label.text }}</text>
            <g :key="`punch-${animKey}`">
              <circle v-for="(dot, index) in punchGeom.dots" :key="dot.key" class="usage-punch-dot" :class="`usage-heat-${dot.level}`" :style="{ animationDelay: `${Math.min(index * 5, 600)}ms` }" :cx="dot.cx" :cy="dot.cy" :r="dot.radius" @pointerenter="hoverCell = dot" @pointerleave="hoverCell = null" />
            </g>
          </svg>
        </div>
        <svg v-else-if="chartName === 'waffle'" :viewBox="`0 0 ${waffleWidth} ${waffleHeight}`" class="usage-chart-svg" role="img" aria-label="供应商消耗构成点阵，一枚圆点约等于百分之一的消耗" preserveAspectRatio="xMidYMid meet">
          <g :key="`waffle-${animKey}`">
            <circle v-for="(dot, index) in waffleDots" :key="dot.key" class="usage-waffle-dot" :class="`usage-waffle-${dot.shade}`" :style="{ animationDelay: `${Math.min(index * 8, 640)}ms` }" :cx="dot.cx" :cy="dot.cy" r="9" />
          </g>
        </svg>
        <svg v-else-if="chartName === 'barcode'" :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="usage-chart-svg" role="img" aria-label="每个分桶一根发丝线的消耗条码图" preserveAspectRatio="xMidYMid meet">
          <g :key="`barcode-${animKey}`">
            <line v-for="(bar, index) in barcodeGeom" :key="bar.key" class="usage-barcode-line" :class="{ 'is-empty': bar.empty }" :x1="bar.x" :x2="bar.x" :y1="bar.y1" :y2="bar.y2" :style="{ animationDelay: `${Math.min(index * 4, 500)}ms` }" />
            <circle v-for="bar in barcodeGeom" v-show="!bar.empty" :key="`${bar.key}-dot`" class="usage-barcode-dot" :cx="bar.x" :cy="bar.y1" :r="bar.radius" />
          </g>
          <text v-for="label in xLabels" :key="label.index" class="usage-axis-text" :x="x(label.index)" :y="chartHeight - 6" :text-anchor="label.index === 0 ? 'start' : label.index === buckets.length - 1 ? 'end' : 'middle'">{{ label.text }}</text>
        </svg>
        <template v-else>
          <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="usage-chart-svg" role="img" :aria-label="`${metric.label}随时间变化曲线`" preserveAspectRatio="xMidYMid meet">
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
        </template>
        <div v-if="hoverCell && hoverCell.bucket" class="usage-tooltip usage-tooltip-heat" :style="heatTooltipStyle">
          <strong>{{ hoverCell.bucket._label || formatBucketTime(hoverCell.bucket.start_at) }}</strong>
          <span>请求 {{ hoverCell.bucket.request_count }} · 合计 {{ Number(hoverCell.bucket.total_tokens || 0).toLocaleString() }} Token</span>
        </div>
      </div>

      <div class="usage-legend-row">
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
        <template v-else-if="chartName === 'cumulative'">
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />累计曲线</span>
        </template>
        <template v-else-if="chartName === 'calendar'">
          <span class="usage-legend-item"><i class="usage-swatch usage-heat-swatch-1" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-2" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-3" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-4" aria-hidden="true" />圆点越大越深 = 当日消耗越高</span>
          <span class="usage-legend-item usage-legend-note">{{ calendarPeakNote }}</span>
        </template>
        <template v-else-if="chartName === 'punch'">
          <span class="usage-legend-item"><i class="usage-swatch usage-heat-swatch-2" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-3" aria-hidden="true" /><i class="usage-swatch usage-heat-swatch-4" aria-hidden="true" />圆点越大越深 = 该星期×小时消耗越高</span>
        </template>
        <template v-else-if="chartName === 'waffle'">
          <span class="usage-legend-item"><i class="usage-swatch usage-waffle-swatch" aria-hidden="true" />一枚圆点 ≈ 1% 消耗，颜色越深 = 排名越靠前</span>
        </template>
        <template v-else>
          <span class="usage-legend-item"><i class="usage-swatch usage-swatch-total" aria-hidden="true" />发丝线 = 一个分桶 · 圆点 = 消耗量</span>
        </template>
        <span class="usage-legend-note">{{ legendNoteTail }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch } from '../api.js'
import TimeRangeSelect from './TimeRangeSelect.vue'
import UiIcon from './ui/UiIcon.vue'
import { formatTokenCount } from '../token-format.js'
import { buildShareCardData } from '../share-card.js'

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
const CALENDAR_LEFT = 40
const CALENDAR_TOP = 22
const CALENDAR_MAX = 26
const PUNCH_LEFT = 36
const PUNCH_TOP = 20
const PUNCH_MAX = 24
const WAFFLE_SIZE = 300
const WAFFLE_GAP = 6
const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
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
const punchMatrix = ref([])
const waffleStatus = ref(null)
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
  { value: 'calendar', label: '日历', hint: 'GitHub 风格日历热力 / 小时方格' },
  { value: 'cumulative', label: '累计', hint: '累计冲击线 + 大数字' },
  { value: 'punch', label: '节律', hint: '星期×24 小时节律点阵' },
  { value: 'waffle', label: '构成', hint: '供应商构成点阵' },
  { value: 'barcode', label: '发丝线', hint: '逐桶发丝线条码' }
]

function normalizeChart(value) {
  if (value === 'calendar' || value === 'punch' || value === 'waffle' || value === 'barcode' || value === 'cumulative') return value
  if (value === 'heat' || value === 'trend' || value === 'area' || value === 'line' || value === 'bars') return value === 'heat' ? 'calendar' : 'trend'
  return 'trend'
}

function quarticOut(t) { return 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 4) }

const metric = computed(() => metrics[metricIndex.value] || metrics[0])
const pillsVisible = computed(() => chartName.value === 'trend' || chartName.value === 'cumulative')
const carouselling = computed(() => !reducedMotion && pillsVisible.value)
const windowLabel = computed(() => windowOptions.find(option => option.value === windowName.value)?.label || (windowName.value === 'custom' ? '自定义时间' : ''))
const headingMeta = computed(() => {
  if (error.value) return '用量趋势暂不可用'
  const unit = granularity.value === 'hour' ? '按小时' : '按天'
  return buckets.value.length ? `${windowLabel.value} · ${unit}分桶 · ${buckets.value.length} 个数据点` : windowLabel.value ? `${windowLabel.value} · ${unit}分桶` : ''
})
const conclusionLabel = computed(() => `${windowLabel.value || '所选范围'} 合计`)
const emptyText = computed(() => {
  if (loading.value) return '正在读取用量数据…'
  if (chartName.value === 'punch') return '所选时间范围内没有 Token 记录，无法计算时段节律'
  return '所选时间范围内没有 Token 记录'
})
const chartEmpty = computed(() => {
  if (chartName.value === 'waffle') return !waffleDots.value.length && !loading.value
  if (chartName.value === 'punch') return punchEmpty.value && !loading.value
  return !buckets.value.length
})
const punchEmpty = computed(() => {
  if (!punchMatrix.value.length) return true
  return punchMatrix.value.every(row => row.every(cell => Number(cell.total_tokens || 0) <= 0))
})

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
const calendarPeakNote = computed(() => {
  const peak = calendarGeom.value.peak
  if (!peak) return ''
  return `最忙 ${formatBucketTime(peak.bucket.start_at)}（${formatTokenCount(Number(peak.bucket.total_tokens || 0))}）`
})
const legendNoteTail = computed(() => {
  if (chartName.value === 'trend' || chartName.value === 'cumulative') return 'y 轴从 0 开始 · 不断轴'
  if (chartName.value === 'barcode') return 'y 轴从 0 开始 · 不断轴 · 安静分桶保留短基线'
  return ''
})

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

const heatGeom = computed(() => buildHeatGeom())
const punchGeom = computed(() => buildPunchGeom())
const calendarGeom = computed(() => buildCalendarGeom())
const barcodeGeom = computed(() => {
  const count = buckets.value.length
  if (!count) return []
  const innerWidth = chartWidth - padLeft - padRight
  const slot = innerWidth / count
  const lineWidth = Math.max(1, Math.min(3, slot * 0.35))
  const base = chartHeight - padBottom
  const maxToken = Math.max(1, ...buckets.value.map(bucket => Number(bucket.total_tokens || 0)))
  const trackTop = padTop + 18
  return buckets.value.map((bucket, index) => {
    const tokens = Number(bucket.total_tokens || 0)
    const x = padLeft + slot * index + slot / 2
    const dotRadius = tokens > 0 ? Math.max(2.5, Math.sqrt(tokens / maxToken) * 9) : 0
    const dotY = tokens > 0 ? base - Math.sqrt(tokens / maxToken) * (base - trackTop) : base
    return {
      key: index,
      x,
      y1: dotY,
      y2: base,
      empty: tokens <= 0,
      radius: dotRadius
    }
  })
})
const waffleDots = computed(() => {
  const entries = waffleProviders.value
  if (!entries.length) return []
  const perRow = 10
  const cell = (WAFFLE_SIZE - WAFFLE_GAP) / perRow
  const dots = []
  let index = 0
  entries.forEach((entry, providerIndex) => {
    const dotsForProvider = Math.max(entry.tokenShare > 0 ? 1 : 0, Math.round((entry.tokenShare / 100) * 100))
    for (let d = 0; d < dotsForProvider && index < 100; d += 1) {
      const row = Math.floor(index / perRow)
      const col = index % perRow
      dots.push({
        key: `${entry.providerId}-${d}`,
        cx: WAFFLE_GAP / 2 + col * cell + cell / 2,
        cy: WAFFLE_GAP / 2 + row * cell + cell / 2,
        shade: Math.min(4, providerIndex + 1)
      })
      index += 1
    }
  })
  return dots
})
const waffleWidth = WAFFLE_SIZE
const waffleHeight = WAFFLE_SIZE
const waffleProviders = computed(() => {
  const status = waffleStatus.value
  if (!status) return []
  return buildShareCardData({ status, providerLimit: 8 }).byProvider
})
const heatTooltipStyle = computed(() => {
  if (!hoverCell.value) return {}
  const cx = hoverCell.value.cx ?? hoverCell.value.x ?? 0
  const width = chartName.value === 'punch' ? punchGeom.value.width : calendarGeom.value.width
  const ratio = cx / width
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})
const tooltipStyle = computed(() => {
  if (hoverIndex.value < 0) return {}
  const ratio = x(hoverIndex.value) / chartWidth
  return ratio > 0.7 ? { right: `${(1 - ratio) * 100}%` } : { left: `${ratio * 100}%` }
})
const lineDrawStyle = computed(() => ({ strokeDasharray: pathLength.value, strokeDashoffset: pathLength.value * (1 - drawProgress.value) }))
const pathLength = ref(0)

function heatLevel(value, maxToken) {
  if (value <= 0) return 0
  const ratio = value / maxToken
  if (ratio > 0.75) return 4
  if (ratio > 0.5) return 3
  if (ratio > 0.25) return 2
  return 1
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
    return finishGridGeom(rows, 24, row => row.label, bucket => new Date(bucket.start_at).getHours())
  }
  return buildCalendarGeom()
}

function buildPunchGeom() {
  const maxToken = Math.max(1, ...punchMatrix.value.flat().map(cell => Number(cell.total_tokens || 0)))
  const availableWidth = chartWidth - PUNCH_LEFT - 8
  const availableHeight = chartHeight - PUNCH_TOP - 8
  const cell = Math.max(14, Math.min(28, Math.floor(Math.min(availableWidth / 24, availableHeight / 7))))
  const dots = []
  punchMatrix.value.forEach((row, weekday) => {
    row.forEach((cellData, hour) => {
      const tokens = Number(cellData.total_tokens || 0)
      dots.push({
        key: `${weekday}-${hour}`,
        cx: PUNCH_LEFT + hour * cell + cell / 2,
        cy: PUNCH_TOP + weekday * cell + cell / 2,
        radius: tokens > 0 ? Math.max(2, Math.sqrt(tokens / maxToken) * (cell / 2 - 2)) : 1.5,
        level: heatLevel(tokens, maxToken),
        bucket: {
          ...cellData,
          start_at: null,
          _label: `${WEEKDAY_LABELS[weekday]} ${String(hour).padStart(2, '0')}:00`
        }
      })
    })
  })
  return {
    cell,
    dots,
    colLabels: [0, 6, 12, 18],
    rowLabels: WEEKDAY_LABELS.map((text, index) => ({ index, text })),
    width: PUNCH_LEFT + 24 * cell + 8,
    height: PUNCH_TOP + 7 * cell + 8
  }
}

function buildCalendarGeom() {
  if (granularity.value === 'hour' || !buckets.value.length) return { dots: [], colLabels: [], rowLabels: [], width: 0, height: 0, left: CALENDAR_LEFT, top: CALENDAR_TOP, cell: 0, peak: null }
  const byDay = new Map()
  for (const bucket of buckets.value) {
    const date = new Date(bucket.start_at)
    const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate())
    const key = dayStart.getTime()
    const existing = byDay.get(key)
    if (existing) {
      existing.tokens += Number(bucket.total_tokens || 0)
      existing.request_count += Number(bucket.request_count || 0)
      existing.successful_requests += Number(bucket.successful_requests || 0)
      existing.failed_requests += Number(bucket.failed_requests || 0)
    } else {
      byDay.set(key, {
        tokens: Number(bucket.total_tokens || 0),
        request_count: Number(bucket.request_count || 0),
        successful_requests: Number(bucket.successful_requests || 0),
        failed_requests: Number(bucket.failed_requests || 0),
        date: dayStart
      })
    }
  }
  const days = [...byDay.entries()].sort((a, b) => a[0] - b[0]).map(([key, value]) => ({ key, ...value }))
  const firstWeekday = (days[0].date.getDay() + 6) % 7
  const weeks = Math.ceil((firstWeekday + days.length) / 7)
  const availableWidth = chartWidth - CALENDAR_LEFT - 8
  const availableHeight = chartHeight - CALENDAR_TOP - 10
  const cell = Math.max(12, Math.min(CALENDAR_MAX, Math.floor(Math.min(availableWidth / Math.max(1, weeks), availableHeight / 7))))
  const maxToken = Math.max(1, ...days.map(day => day.tokens))
  const dotRadiusMax = cell / 2 - 2
  const dots = []
  let peak = null
  days.forEach((day, index) => {
    const slot = firstWeekday + index
    const week = Math.floor(slot / 7)
    const weekday = slot % 7
    const radius = day.tokens > 0 ? Math.max(2, Math.sqrt(day.tokens / maxToken) * dotRadiusMax) : 1.5
    const dot = {
      key: String(day.key),
      cx: CALENDAR_LEFT + week * cell + cell / 2,
      cy: CALENDAR_TOP + weekday * cell + cell / 2,
      radius,
      level: heatLevel(day.tokens, maxToken),
      bucket: {
        start_at: day.date.getTime(),
        total_tokens: day.tokens,
        request_count: day.request_count,
        successful_requests: day.successful_requests,
        failed_requests: day.failed_requests
      }
    }
    dots.push(dot)
    if (!peak || day.tokens > peak.tokens) peak = { ...dot, tokens: day.tokens }
  })
  const monthLabels = []
  let lastMonth = -1
  days.forEach((day, index) => {
    const slot = firstWeekday + index
    const week = Math.floor(slot / 7)
    if (day.date.getMonth() !== lastMonth) {
      lastMonth = day.date.getMonth()
      if (!monthLabels.some(label => label.index === week)) {
        monthLabels.push({ index: week, text: `${lastMonth + 1}月` })
      }
    }
  })
  return {
    cell,
    dots,
    colLabels: monthLabels,
    rowLabels: ['周一', '周三', '周五', '周日'].map((text, index) => ({ index: index * 2, text })),
    width: CALENDAR_LEFT + weeks * cell + 8,
    height: CALENDAR_TOP + 7 * cell + 8,
    left: CALENDAR_LEFT,
    top: CALENDAR_TOP,
    peak: peak && peak.tokens > 0 ? peak : null
  }
}

function finishGridGeom(rows, fixedColumns, rowLabelOf, hourOf) {
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
  if (value == null) return ''
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
  loadAuxData()
  replayEntrance()
}
function changeMetric(index) {
  metricIndex.value = index
  replayEntrance()
}
function changeWindow(value) {
  if (value !== 'custom') customRange.value = null
  localStorage.setItem('local-proxy-usage-trend-window', value)
  loadAll()
}
function applyCustomRange(range) {
  customRange.value = range
  windowName.value = 'custom'
  localStorage.setItem('local-proxy-usage-trend-window', 'custom')
  loadAll()
}
function timelineParams() {
  const params = new URLSearchParams({ usage_window: windowName.value })
  if (windowName.value === 'custom' && customRange.value) {
    params.set('start_at', String(customRange.value.startAt))
    params.set('end_at', String(customRange.value.endAt))
  }
  return params
}
function windowParams() {
  return timelineParams()
}
async function loadTimeline() {
  if (windowName.value === 'custom' && !customRange.value) return
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
  }
}
async function loadAuxData() {
  error.value = ''
  try {
    if (chartName.value === 'punch') {
      const payload = await controlFetch(`/api/usage-weekday-hour?${windowParams()}`)
      punchMatrix.value = Array.isArray(payload.matrix) ? payload.matrix : []
    } else if (chartName.value === 'waffle') {
      waffleStatus.value = await controlFetch(`/api/status?${windowParams()}`).catch(() => null)
    }
  } catch (requestError) {
    error.value = requestError.message
  }
}
async function loadAll() {
  if (document.hidden) return
  if (loading.value) { reloadAfterCurrent = true; return }
  if (windowName.value === 'custom' && !customRange.value) return
  loading.value = true
  await loadTimeline()
  await loadAuxData()
  loading.value = false
  if (reloadAfterCurrent) { reloadAfterCurrent = false; void loadAll() }
}
onMounted(() => {
  loadAll()
  timer = window.setInterval(loadAll, 30000)
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
