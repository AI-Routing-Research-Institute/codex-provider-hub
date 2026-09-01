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
        <canvas v-show="!loading" ref="canvasElement" class="share-card-canvas" width="600" height="940" aria-label="今日 Token 战报海报预览" />
      </div>
      <p v-if="!loading && data && !data.hasData" class="share-card-empty">今天还没有请求记录，先领一张零消耗战报也不错。</p>
      <p v-if="notice" class="share-card-notice" :class="{ error: notice.tone === 'error' }" role="status">{{ notice.detail }}</p>
      <div class="share-card-actions">
        <button class="secondary-button" type="button" :disabled="loading || !data" @click="downloadCard">{{ downloadState || '下载海报' }}</button>
        <button class="primary-button" type="button" :disabled="loading || !data" @click="copyCard">{{ copyState || '复制海报' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch } from '../api.js'
import { formatTokenCount } from '../token-format.js'
import {
  buildShareCardData,
  formatGroupedTokens,
  latestBattleLabel,
  posterStars,
  shareBadgeTitle,
  shareCardFileName,
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
    const [status, uiConfig, yesterdayStatus] = await Promise.all([
      controlFetch('/api/status?usage_window=today'),
      controlFetch('/api/ui-config').catch(() => ({})),
      controlFetch(`/api/status?usage_window=custom&start_at=${yesterdayStart}&end_at=${todayStart}`).catch(() => null)
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
      latestBattle: latestBattleLabel(status?.usage?.total?.last_request_at)
    }
    loading.value = false
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

function paintGlow(context, x, y, radius, color) {
  const glow = context.createRadialGradient(x, y, 0, x, y, radius)
  glow.addColorStop(0, color)
  glow.addColorStop(1, 'rgba(0,0,0,0)')
  context.fillStyle = glow
  context.fillRect(x - radius, y - radius, radius * 2, radius * 2)
}

function drawPosterBackdrop(context, width, height) {
  const background = context.createLinearGradient(0, 0, width * 0.4, height)
  background.addColorStop(0, '#0a0a26')
  background.addColorStop(0.42, '#2c1358')
  background.addColorStop(0.78, '#6b2180')
  background.addColorStop(1, '#a3346e')
  context.fillStyle = background
  context.fillRect(0, 0, width, height)
  paintGlow(context, width * 0.86, height * 0.12, 300, 'rgba(236, 72, 153, 0.38)')
  paintGlow(context, width * 0.1, height * 0.52, 340, 'rgba(99, 102, 241, 0.32)')
  paintGlow(context, width * 0.72, height * 0.88, 320, 'rgba(251, 191, 36, 0.26)')
  paintGlow(context, width * 0.18, height * 0.06, 200, 'rgba(34, 211, 238, 0.2)')
  for (const star of posterStars({ count: 64, width, height })) {
    context.globalAlpha = star.alpha
    context.fillStyle = '#ffffff'
    context.beginPath()
    context.arc(star.x, star.y, star.radius, 0, Math.PI * 2)
    context.fill()
    if (star.sparkle) {
      context.globalAlpha = Math.min(0.9, star.alpha + 0.2)
      context.strokeStyle = '#ffffff'
      context.lineWidth = 1
      const arm = star.radius * 3.4
      context.beginPath()
      context.moveTo(star.x - arm, star.y)
      context.lineTo(star.x + arm, star.y)
      context.moveTo(star.x, star.y - arm)
      context.lineTo(star.x, star.y + arm)
      context.stroke()
    }
  }
  context.globalAlpha = 1
}

function drawCard() {
  const canvas = canvasElement.value
  const card = data.value
  if (!canvas || !card) return
  const width = 600
  const height = 940
  const scale = Math.max(2, Math.min(3, Math.round(window.devicePixelRatio || 1) + 1))
  canvas.width = width * scale
  canvas.height = height * scale
  canvas.style.aspectRatio = `${width} / ${height}`
  const context = canvas.getContext('2d')
  context.scale(scale, scale)
  const fontFamily = '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif'
  context.textBaseline = 'alphabetic'
  context.textAlign = 'left'

  drawPosterBackdrop(context, width, height)

  const accent = context.createLinearGradient(90, 0, width - 90, 0)
  accent.addColorStop(0, '#fbbf24')
  accent.addColorStop(0.5, '#f472b6')
  accent.addColorStop(1, '#38bdf8')

  context.fillStyle = 'rgba(255, 255, 255, 0.72)'
  context.font = `700 15px ${fontFamily}`
  if ('letterSpacing' in context) context.letterSpacing = '5px'
  context.fillText(`${String(card.brandMark).slice(0, 4)} · 今日 TOKEN 战报`, 56, 76)
  if ('letterSpacing' in context) context.letterSpacing = '0px'

  context.fillStyle = '#ffffff'
  context.font = `800 34px ${fontFamily}`
  context.fillText(`${card.dateLabel} ${card.weekdayLabel}`, 56, 126)
  context.fillStyle = 'rgba(255, 255, 255, 0.55)'
  context.font = `400 14px ${fontFamily}`
  context.fillText('这一天，你和模型并肩作战', 56, 154)

  context.fillStyle = 'rgba(255, 255, 255, 0.4)'
  context.font = `500 26px ${fontFamily}`
  context.fillText('今日狂飙', 56, 250)
  context.save()
  context.shadowColor = 'rgba(251, 191, 36, 0.55)'
  context.shadowBlur = 34
  const numberSize = card.totalTokens > 99_999_999 ? 74 : card.totalTokens > 9_999_999 ? 84 : 96
  context.font = `900 ${numberSize}px ${fontFamily}`
  context.fillStyle = '#ffffff'
  context.fillText(formatGroupedTokens(card.totalTokens), 56, 350)
  context.restore()
  context.fillStyle = accent
  context.font = `800 20px ${fontFamily}`
  if ('letterSpacing' in context) context.letterSpacing = '8px'
  context.fillText('TOKENS', 58, 392)
  if ('letterSpacing' in context) context.letterSpacing = '0px'

  const badgeWidth = 54 + card.badgeTitle.length * 22
  const badge = context.createLinearGradient(56, 0, 56 + badgeWidth, 0)
  badge.addColorStop(0, '#f59e0b')
  badge.addColorStop(1, '#ec4899')
  roundedRect(context, 56, 424, badgeWidth, 42, 21)
  context.fillStyle = badge
  context.fill()
  context.fillStyle = '#1c1030'
  context.font = `800 17px ${fontFamily}`
  context.textAlign = 'center'
  context.fillText(card.badgeTitle, 56 + badgeWidth / 2, 452)
  context.textAlign = 'left'

  if (card.yesterday?.direction) {
    const tone = card.yesterday.direction === 'up' ? '#4ade80' : card.yesterday.direction === 'down' ? '#fca5a5' : 'rgba(255,255,255,0.75)'
    const arrow = card.yesterday.direction === 'up' ? '↑' : card.yesterday.direction === 'down' ? '↓' : '→'
    context.fillStyle = 'rgba(255, 255, 255, 0.14)'
    roundedRect(context, 56, 486, 54 + card.yesterday.label.length * 17, 34, 17)
    context.fill()
    context.fillStyle = tone
    context.font = `700 15px ${fontFamily}`
    context.fillText(`${arrow} ${card.yesterday.label}`, 72, 509)
  }

  const capsules = [
    ['输入', card.inputTokens],
    ['输出', card.outputTokens],
    ['缓存', card.cachedTokens]
  ]
  const capsuleTop = 548
  const capsuleWidth = (width - 112 - 2 * 14) / 3
  capsules.forEach(([label, value], index) => {
    const left = 56 + index * (capsuleWidth + 14)
    context.fillStyle = 'rgba(255, 255, 255, 0.1)'
    roundedRect(context, left, capsuleTop, capsuleWidth, 62, 14)
    context.fill()
    context.strokeStyle = 'rgba(255, 255, 255, 0.16)'
    context.lineWidth = 1
    context.stroke()
    context.fillStyle = 'rgba(255, 255, 255, 0.6)'
    context.font = `500 13px ${fontFamily}`
    context.fillText(label, left + 14, capsuleTop + 24)
    context.fillStyle = '#ffffff'
    context.font = `700 20px ${fontFamily}`
    context.fillText(formatTokenCount(value), left + 14, capsuleTop + 48)
  })

  context.fillStyle = 'rgba(255, 255, 255, 0.4)'
  context.font = `500 24px ${fontFamily}`
  context.fillText('今日消耗构成', 56, 678)

  const donutCenterX = 152
  const donutCenterY = 800
  const donutRadius = 74
  const donutWidth = 22
  const palette = ['#f472b6', '#a78bfa', '#38bdf8', '#fbbf24', '#34d399']
  const providerEntries = card.byProvider.slice(0, 5)
  const donutTotal = providerEntries.reduce((sum, entry) => sum + entry.totalTokens, 0) || 1
  if (providerEntries.length) {
    let angle = -Math.PI / 2
    providerEntries.forEach((entry, index) => {
      const slice = (entry.totalTokens / donutTotal) * Math.PI * 2
      context.beginPath()
      context.strokeStyle = palette[index % palette.length]
      context.lineWidth = donutWidth
      context.lineCap = 'butt'
      context.arc(donutCenterX, donutCenterY, donutRadius, angle, angle + slice)
      context.stroke()
      angle += slice
    })
    context.beginPath()
    context.arc(donutCenterX, donutCenterY, donutRadius - donutWidth / 2 - 4, 0, Math.PI * 2)
    context.strokeStyle = 'rgba(255,255,255,0.14)'
    context.lineWidth = 1
    context.stroke()
    context.fillStyle = '#ffffff'
    context.font = `800 22px ${fontFamily}`
    context.textAlign = 'center'
    context.fillText(`${providerEntries.length} 家`, donutCenterX, donutCenterY - 2)
    context.fillStyle = 'rgba(255, 255, 255, 0.55)'
    context.font = `500 12px ${fontFamily}`
    context.fillText('供应商出战', donutCenterX, donutCenterY + 20)
    context.textAlign = 'left'
  } else {
    context.strokeStyle = 'rgba(255,255,255,0.2)'
    context.lineWidth = donutWidth
    context.beginPath()
    context.arc(donutCenterX, donutCenterY, donutRadius, 0, Math.PI * 2)
    context.stroke()
  }

  const legendLeft = 268
  let legendTop = 726
  if (providerEntries.length) {
    context.fillStyle = 'rgba(255, 255, 255, 0.62)'
    context.font = `500 13px ${fontFamily}`
    context.fillText('本命供应商', legendLeft, legendTop)
    context.fillStyle = '#ffffff'
    context.font = `800 22px ${fontFamily}`
    let champion = providerEntries[0].name
    while (champion.length > 12 && context.measureText(champion).width > 240) champion = `${champion.slice(0, -2)}…`
    context.fillText(champion, legendLeft, legendTop + 28)
    context.fillStyle = '#fde68a'
    context.font = `800 16px ${fontFamily}`
    context.fillText(`独挑 ${providerEntries[0].tokenShare}% 今日用量`, legendLeft, legendTop + 52)
    legendTop += 84
    providerEntries.slice(1, 4).forEach((entry, index) => {
      const top = legendTop + index * 30
      context.fillStyle = palette[(index + 1) % palette.length]
      roundedRect(context, legendLeft, top - 10, 8, 8, 2)
      context.fill()
      context.fillStyle = 'rgba(255, 255, 255, 0.85)'
      context.font = `600 14px ${fontFamily}`
      let name = entry.name
      while (name.length > 10 && context.measureText(name).width > 150) name = `${name.slice(0, -2)}…`
      context.fillText(name, legendLeft + 16, top)
      context.fillStyle = 'rgba(255, 255, 255, 0.5)'
      context.font = `500 13px ${fontFamily}`
      context.textAlign = 'right'
      context.fillText(`${formatTokenCount(entry.totalTokens)} · ${entry.tokenShare}%`, width - 56, top)
      context.textAlign = 'left'
    })
  } else {
    context.fillStyle = 'rgba(255, 255, 255, 0.55)'
    context.font = `500 14px ${fontFamily}`
    context.fillText('今天还没有供应商出战', legendLeft, legendTop + 20)
  }

  if (card.latestBattle) {
    context.fillStyle = 'rgba(255, 255, 255, 0.5)'
    context.font = `500 15px ${fontFamily}`
    context.fillText(`最晚一战 ${card.latestBattle} · 你还在敲代码`, 56, 888)
  }

  const footerLine = context.createLinearGradient(56, 0, width - 56, 0)
  footerLine.addColorStop(0, 'rgba(255,255,255,0.05)')
  footerLine.addColorStop(0.5, 'rgba(255,255,255,0.28)')
  footerLine.addColorStop(1, 'rgba(255,255,255,0.05)')
  context.fillStyle = footerLine
  context.fillRect(56, 906, width - 112, 1)
  context.fillStyle = 'rgba(255, 255, 255, 0.72)'
  context.font = `700 14px ${fontFamily}`
  context.fillText(`${card.displayName} · 本地中转战报`, 56, 900 - 8)
  context.fillStyle = 'rgba(255, 255, 255, 0.45)'
  context.font = `400 12px ${fontFamily}`
  context.fillText('数据仅保存在本机 · 成功率与请求统计详见控制台', 56, 930)

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
