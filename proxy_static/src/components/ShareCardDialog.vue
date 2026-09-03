<template>
  <div class="share-card-modal" role="dialog" aria-modal="true" aria-labelledby="share-card-title" @click.self="$emit('close')">
    <div class="share-card-dialog">
      <div class="share-card-heading">
        <div><strong id="share-card-title">今日 Token 战报</strong><span>{{ subtitle }}</span></div>
        <button class="usage-history-close" type="button" aria-label="关闭战报海报" @click="$emit('close')"><UiIcon name="close" /></button>
      </div>
      <p v-if="error" class="share-card-error" role="alert">{{ error }}</p>
      <div class="share-card-stage">
        <div v-if="loading" class="share-card-placeholder">正在生成战报…</div>
        <canvas v-show="!loading" ref="canvasElement" class="share-card-canvas share-card-canvas-terminal" width="360" height="640" aria-label="今日 Token 战报海报预览" />
      </div>
      <p v-if="!loading && data && !data.hasData" class="share-card-empty">今天还没有请求记录，先领一张零消耗战报也不错。</p>
      <p v-if="notice" class="share-card-notice" :class="{ error: notice.tone === 'error' }" role="status">{{ notice.detail }}</p>
      <div class="share-card-actions share-card-actions-terminal">
        <button class="share-terminal-button" type="button" :disabled="loading || !data" @click="downloadCard">{{ downloadState || '下载海报' }}</button>
        <button class="share-terminal-button primary" type="button" :disabled="loading || !data" @click="copyCard">{{ copyState || '复制海报' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlBase, controlFetch } from '../api.js'
import { formatTokenCount } from '../token-format.js'
import {
  buildShareCardData,
  formatGroupedTokens,
  latestBattleLabel,
  shareBadgeTitle,
  shareCardFileName,
  terminalCardTheme,
  yesterdayCompare
} from '../share-card.js'
import UiIcon from './ui/UiIcon.vue'

const emit = defineEmits(['close'])
const canvasElement = ref(null)
const loading = ref(true)
const error = ref('')
const copyState = ref('')
const downloadState = ref('')
const notice = ref(null)
const data = ref(null)
const subtitle = ref('数据统计自今日 00:00 起 · 对比为昨日全天')
let canvasBlob = null
let keyHandler

onMounted(async () => {
  keyHandler = event => { if (event.key === 'Escape') emit('close') }
  window.addEventListener('keydown', keyHandler)
  try {
    const now = new Date()
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    const yesterdayStart = todayStart - 86_400_000
    const [status, uiConfig, yesterdayStatus, timeline] = await Promise.all([
      controlFetch('/api/status?usage_window=today'),
      controlFetch('/api/ui-config').catch(() => ({})),
      controlFetch(`/api/status?usage_window=custom&start_at=${yesterdayStart}&end_at=${todayStart}`).catch(() => null),
      controlFetch('/api/usage-timeline?usage_window=today').catch(() => null)
    ])
    const todayTokens = status?.usage?.total?.total_tokens
    const yesterdayTokens = Number(yesterdayStatus?.usage?.total?.total_tokens || 0)
    data.value = {
      ...buildShareCardData({
        status: {
          ...status,
          display_name: uiConfig.display_name || status.service,
          brand_mark: uiConfig.brand_mark || 'CX'
        },
        now
      }),
      badgeTitle: shareBadgeTitle(todayTokens),
      yesterday: yesterdayCompare(todayTokens, yesterdayTokens),
      latestBattle: latestBattleLabel(status?.usage?.total?.last_request_at),
      hourly: Array.isArray(timeline?.buckets) ? timeline.buckets : []
    }
    loading.value = false
    try {
      await Promise.race([document.fonts.ready, new Promise(resolve => window.setTimeout(resolve, 1200))])
    } catch { /* 字体加载失败时回退系统等宽栈 */ }
    await nextTick()
    drawCard()
  } catch (loadError) {
    loading.value = false
    error.value = loadError.message || '无法读取今日用量'
  }
})

onBeforeUnmount(() => { window.removeEventListener('keydown', keyHandler) })

function roundedRect(context, x, y, width, height, radius) {
  if (typeof context.roundRect === 'function') { context.beginPath(); context.roundRect(x, y, width, height, radius); return }
  context.beginPath()
  context.moveTo(x + radius, y)
  context.arcTo(x + width, y, x + width, y + height, radius)
  context.arcTo(x + width, y + height, x, y + height, radius)
  context.arcTo(x, y + height, x, y, radius)
  context.arcTo(x, y, x + width, y, radius)
  context.closePath()
}

function fitText(context, text, maxWidth, baseSize, fontFamily, weight = '600') {
  let size = baseSize
  let label = String(text)
  context.font = `${weight} ${size}px ${fontFamily}`
  while (size > 10 && context.measureText(label).width > maxWidth) {
    if (label.length > 4) label = `${label.slice(0, Math.max(3, label.length - 2))}…`
    else size -= 1
    context.font = `${weight} ${size}px ${fontFamily}`
  }
  return label
}

function drawTitlebar(context, theme) {
  const { titlebarHeight, colors, fonts, cornerRadius } = theme
  context.save()
  context.beginPath()
  if (typeof context.roundRect === 'function') context.roundRect(-1, -1, theme.cardWidth + 2, titlebarHeight + cornerRadius + 1, [cornerRadius, cornerRadius, 0, 0])
  else context.rect(0, 0, theme.cardWidth, titlebarHeight)
  context.clip()
  context.fillStyle = colors.panel2
  context.fillRect(0, 0, theme.cardWidth, titlebarHeight)
  context.fillStyle = colors.border
  context.fillRect(0, titlebarHeight - 1, theme.cardWidth, 1)
  context.restore()
  const dotY = titlebarHeight / 2
  const dotColors = [colors.dotR, colors.dotY, colors.dotG]
  dotColors.forEach((color, index) => {
    context.fillStyle = color
    context.beginPath()
    context.arc(16 + 4.5 + index * 15, dotY, 4.5, 0, Math.PI * 2)
    context.fill()
  })
  const serviceTag = controlBase.replace('/control/', '') || 'codex'
  context.font = `400 12px ${fonts.mono}`
  context.fillStyle = colors.muted2
  context.textAlign = 'left'
  context.fillText(`${serviceTag} — today's-usage.log`, 16 + 3 * 9 + 2 * 6 + 10, dotY + 4)
}

function drawCard() {
  const canvas = canvasElement.value
  const card = data.value
  if (!canvas || !card) return
  const theme = terminalCardTheme()
  const { colors, fonts, cardWidth, cardHeight, cornerRadius } = theme
  const scale = Math.max(2, Math.min(3, Math.round(window.devicePixelRatio || 1) + 1))
  const bodyLeft = theme.bodyPaddingX
  const bodyRight = cardWidth - theme.bodyPaddingX
  const bodyWidth = cardWidth - theme.bodyPaddingX * 2

  canvas.width = cardWidth * scale
  canvas.height = cardHeight * scale
  canvas.style.aspectRatio = `${cardWidth} / ${cardHeight}`
  const context = canvas.getContext('2d')
  context.scale(scale, scale)
  context.textBaseline = 'alphabetic'
  context.textAlign = 'left'

  // 满幅圆角卡片：无透明边距，预览阴影由 CSS 提供
  context.save()
  roundedRect(context, 0, 0, cardWidth, cardHeight, cornerRadius)
  context.clip()
  context.fillStyle = colors.panel
  context.fillRect(0, 0, cardWidth, cardHeight)

  drawTitlebar(context, theme)

  // 竖版布局（9:16）
  const datelineY = 68
  const heroNumY = 130
  const heroLabelY = 154
  const diffY = 184
  const divider1Y = 212
  const statsRows = [240, 268, 296]
  const metaY = 328
  const breakdownHdY = 362
  const breakdownBarY = 374
  const breakdownSrcY = 398
  const hourlyLabelY = 436
  const hourlyTop = 450
  const hourlyHeight = 88
  const hourlyTicksY = 554
  const footerDividerY = 592
  const footerY = 620

  context.font = `500 12.5px ${fonts.mono}`
  context.fillStyle = colors.good
  context.fillText('$', bodyLeft, datelineY)
  const promptWidth = context.measureText('$ ').width
  context.fillStyle = colors.text
  const dateText = String(card.dateLabel || '').replace(/\//g, '-')
  context.fillText(dateText, bodyLeft + promptWidth, datelineY)
  const dateWidth = context.measureText(dateText).width
  context.fillStyle = colors.muted
  context.fillText(` ${card.weekdayLabel} · 统计自今日 00:00 起`, bodyLeft + promptWidth + dateWidth, datelineY)

  const heroGradient = context.createLinearGradient(bodyLeft, heroNumY - 48, bodyRight, heroNumY)
  heroGradient.addColorStop(0, colors.ember1)
  heroGradient.addColorStop(1, colors.ember2)
  const heroText = formatGroupedTokens(card.totalTokens)
  let heroSize = 56
  context.font = `700 ${heroSize}px ${fonts.display}`
  while (heroSize > 32 && context.measureText(heroText).width > bodyWidth) {
    heroSize -= 2
    context.font = `700 ${heroSize}px ${fonts.display}`
  }
  context.fillStyle = heroGradient
  context.fillText(heroText, bodyLeft, heroNumY)
  context.font = `500 13px ${fonts.mono}`
  context.fillStyle = colors.muted
  context.fillText(`tokens 消耗 · ${card.badgeTitle}`, bodyLeft, heroLabelY)

  context.font = `500 12.5px ${fonts.mono}`
  const diff = card.yesterday || {}
  if (diff.direction === 'up' || diff.direction === 'down') {
    const arrow = diff.direction === 'up' ? '▲' : '▼'
    context.fillStyle = colors.good
    context.fillText(arrow, bodyLeft, diffY)
    const arrowWidth = context.measureText(arrow).width
    context.font = `600 12.5px ${fonts.mono}`
    const percentText = `${Math.abs(diff.percent)}%`
    context.fillText(percentText, bodyLeft + arrowWidth + 8, diffY)
    const percentWidth = context.measureText(percentText).width
    context.font = `500 12.5px ${fonts.mono}`
    context.fillStyle = colors.muted2
    context.fillText('较昨日全天', bodyLeft + arrowWidth + 8 + percentWidth + 8, diffY)
  } else if (diff.label) {
    context.fillStyle = colors.muted
    context.fillText(diff.direction === 'flat' ? `▬ ${diff.label}` : diff.label, bodyLeft, diffY)
  }

  context.fillStyle = colors.border
  context.fillRect(bodyLeft, divider1Y, bodyWidth, 1)

  const statRows = [
    { label: '输入', value: card.inputTokens, color: colors.ember2 },
    { label: '输出', value: card.outputTokens, color: colors.out },
    { label: '缓存', value: card.cachedTokens, color: colors.cache }
  ]
  statRows.forEach((row, index) => {
    const rowY = statsRows[index]
    context.fillStyle = row.color
    context.fillRect(bodyLeft, rowY - 8, 6, 6)
    context.font = `500 14px ${fonts.mono}`
    context.fillStyle = colors.muted
    context.fillText(row.label, bodyLeft + 6 + 10, rowY)
    const valueText = formatTokenCount(row.value)
    context.font = `600 14px ${fonts.mono}`
    context.fillStyle = colors.text
    context.fillText(valueText, bodyRight - context.measureText(valueText).width, rowY)
  })

  const metaParts = [`${formatGroupedTokens(card.requestCount)} 次请求`]
  if (card.requestCount > 0) metaParts.push(`成功率 ${card.successRate}%`)
  if (card.failedRequests) metaParts.push(`失败 ${formatGroupedTokens(card.failedRequests)} 次`)
  if (card.estimatedRequests) metaParts.push('含估算')
  context.font = `400 11.5px ${fonts.mono}`
  context.fillStyle = colors.muted2
  context.fillText(metaParts.join(' · '), bodyLeft, metaY)

  context.font = `500 12px ${fonts.mono}`
  context.fillStyle = colors.muted
  context.fillText('今日消耗构成', bodyLeft, breakdownHdY)
  const topProvider = card.byProvider[0] || null
  const providerCount = card.byProvider.length
  if (topProvider) {
    const shareText = `${topProvider.tokenShare}%`
    const nameText = fitText(context, ` ${topProvider.name}`, bodyWidth * 0.55, 12, fonts.mono, '600')
    context.font = `600 12px ${fonts.mono}`
    context.fillStyle = colors.text
    const fullText = `${shareText}${nameText}`
    context.fillText(fullText, bodyRight - context.measureText(fullText).width, breakdownHdY)
  }
  roundedRect(context, bodyLeft, breakdownBarY, bodyWidth, 8, 4)
  context.fillStyle = colors.panel2
  context.fill()
  context.strokeStyle = colors.border
  context.lineWidth = 1
  context.stroke()
  const fillRatio = topProvider ? Math.max(topProvider.tokenShare > 0 ? 0.02 : 0, topProvider.tokenShare / 100) : 0
  if (fillRatio > 0) {
    const barFill = context.createLinearGradient(bodyLeft, 0, bodyLeft + bodyWidth, 0)
    barFill.addColorStop(0, colors.ember1)
    barFill.addColorStop(1, colors.out)
    context.fillStyle = barFill
    roundedRect(context, bodyLeft + 0.5, breakdownBarY + 0.5, Math.max(4, (bodyWidth - 1) * fillRatio), 7, 3.5)
    context.fill()
  }
  context.font = `400 11.5px ${fonts.mono}`
  context.fillStyle = colors.muted2
  const sourceText = providerCount > 1
    ? `共 ${providerCount} 家供应商 · ${fitText(context, topProvider.name, 160, 11.5, fonts.mono, '400')} 领跑`
    : providerCount === 1 ? '独揽今日全部用量' : '今日无出战'
  context.fillText(sourceText, bodyLeft, breakdownSrcY)

  // 今日时段分布：24 个小时位细条（真实 hourly 数据）
  context.font = `500 12px ${fonts.mono}`
  context.fillStyle = colors.muted
  context.fillText('今日时段分布', bodyLeft, hourlyLabelY)
  const hourlyBuckets = Array.isArray(card.hourly) ? card.hourly : []
  const slot = bodyWidth / 24
  const maxHourly = Math.max(0, ...hourlyBuckets.map(bucket => Number(bucket.total_tokens || 0)))
  if (maxHourly > 0) {
    const hourFill = context.createLinearGradient(bodyLeft, 0, bodyRight, 0)
    hourFill.addColorStop(0, colors.ember1)
    hourFill.addColorStop(1, colors.out)
    let peakHour = 0
    hourlyBuckets.forEach(bucket => {
      const hour = new Date(bucket.start_at).getHours()
      if (Number(bucket.total_tokens || 0) > Number(hourlyBuckets[peakHour]?.total_tokens || 0)) peakHour = hour
    })
    hourlyBuckets.forEach(bucket => {
      const hour = new Date(bucket.start_at).getHours()
      const tokens = Number(bucket.total_tokens || 0)
      const x = bodyLeft + hour * slot + 2
      const barWidth = Math.max(3, slot - 4)
      if (tokens > 0) {
        const barHeight = Math.max(4, Math.round((tokens / maxHourly) * hourlyHeight))
        context.fillStyle = hourFill
        context.fillRect(x, hourlyTop + hourlyHeight - barHeight, barWidth, barHeight)
      } else {
        context.fillStyle = colors.panel2
        context.fillRect(x, hourlyTop + hourlyHeight - 3, barWidth, 3)
      }
    })
    const peakBucket = hourlyBuckets.find(bucket => new Date(bucket.start_at).getHours() === peakHour)
    const peakText = `峰值 ${formatTokenCount(Number(peakBucket?.total_tokens || 0))} · ${String(peakHour).padStart(2, '0')}:00`
    context.font = `400 11.5px ${fonts.mono}`
    context.fillStyle = colors.muted2
    context.fillText(peakText, bodyRight - context.measureText(peakText).width, hourlyLabelY)
    context.font = `400 10.5px ${fonts.mono}`
    context.fillStyle = colors.muted2
    for (const hour of [0, 6, 12, 18]) {
      const label = String(hour).padStart(2, '0')
      context.fillText(label, bodyLeft + hour * slot + 2, hourlyTicksY)
    }
    const label23 = '23'
    context.fillText(label23, bodyRight - context.measureText(label23).width, hourlyTicksY)
  } else {
    context.font = `400 11.5px ${fonts.mono}`
    context.fillStyle = colors.muted2
    context.fillText('今日暂无时段数据', bodyLeft, hourlyTop + 14)
  }

  context.fillStyle = colors.border
  context.fillRect(bodyLeft, footerDividerY, bodyWidth, 1)
  context.font = `400 11.5px ${fonts.mono}`
  context.fillStyle = colors.muted2
  context.fillText(card.latestBattle ? `最晚一战 ${card.latestBattle}` : '今日暂无战事', bodyLeft, footerY)
  const typingText = '你正在敲代码'
  const typingWidth = context.measureText(typingText).width
  context.fillText(typingText, bodyRight - typingWidth - 4 - 6, footerY)
  context.fillStyle = colors.good
  context.fillRect(bodyRight - typingWidth - 4 - 6 + typingWidth + 4, footerY - 10, 6, 12)

  context.fillStyle = 'rgba(255, 255, 255, 0.012)'
  for (let scanY = 0; scanY < cardHeight; scanY += 3) {
    context.fillRect(0, scanY, cardWidth, 1)
  }
  context.restore()

  context.strokeStyle = colors.border
  context.lineWidth = 1
  roundedRect(context, 0.5, 0.5, cardWidth - 1, cardHeight - 1, cornerRadius)
  context.stroke()

  canvas.toBlob(blob => { canvasBlob = blob }, 'image/png')
}

function refreshBlob() {
  return new Promise(resolve => {
    if (canvasBlob) { resolve(canvasBlob); return }
    const canvas = canvasElement.value
    if (!canvas) { resolve(null); return }
    canvas.toBlob(blob => { canvasBlob = blob; resolve(blob) }, 'image/png')
  })
}

async function downloadCard() {
  const blob = await refreshBlob()
  if (!blob) { notice.value = { detail: '画布不可用，请重新打开战报窗口', tone: 'error' }; return }
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = shareCardFileName()
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(link.href), 4000)
  const fileName = shareCardFileName()
  downloadState.value = '已下载'
  notice.value = { detail: `已保存 ${fileName}`, tone: 'success' }
  window.setTimeout(() => { downloadState.value = '' }, 2200)
}

async function copyCard() {
  copyState.value = '复制中…'
  try {
    const blob = await refreshBlob()
    if (!blob || !navigator.clipboard || typeof window.ClipboardItem === 'undefined') throw new Error('clipboard-unsupported')
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
    copyState.value = '已复制'
    notice.value = { detail: '战报已复制，可以直接粘贴到聊天或文档', tone: 'success' }
    window.setTimeout(() => { copyState.value = '' }, 2200)
  } catch (copyError) {
    copyState.value = ''
    notice.value = {
      detail: copyError.message === 'clipboard-unsupported' ? '当前浏览器不支持复制图片，请使用下载海报。' : '复制失败，请使用下载海报。',
      tone: 'error'
    }
  }
}
</script>
