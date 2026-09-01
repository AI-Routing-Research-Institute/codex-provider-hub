<template>
  <div class="share-card-modal" role="dialog" aria-modal="true" aria-labelledby="share-card-title" @click.self="$emit('close')">
    <div class="share-card-dialog">
      <div class="share-card-heading">
        <div><strong id="share-card-title">今日 Token 分享卡片</strong><span>{{ subtitle }}</span></div>
        <button class="usage-history-close" type="button" aria-label="关闭分享卡片" @click="$emit('close')"><UiIcon name="close" /></button>
      </div>
      <p v-if="error" class="share-card-error" role="alert">{{ error }}</p>
      <div class="share-card-stage">
        <div v-if="loading" class="share-card-placeholder">正在生成卡片…</div>
        <canvas v-show="!loading" ref="canvasElement" class="share-card-canvas" width="600" height="800" aria-label="今日 Token 消耗卡片预览" />
      </div>
      <p v-if="!loading && data && !data.hasData" class="share-card-empty">今天还没有请求记录，先生成一张零消耗卡片也不错。</p>
      <p v-if="notice" class="share-card-notice" :class="{ error: notice.tone === 'error' }" role="status">{{ notice.detail }}</p>
      <div class="share-card-actions">
        <button class="secondary-button" type="button" :disabled="loading || !data" @click="downloadCard">{{ downloadState || '下载图片' }}</button>
        <button class="primary-button" type="button" :disabled="loading || !data" @click="copyCard">{{ copyState || '复制图片' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch } from '../api.js'
import { formatTokenCount } from '../token-format.js'
import { buildShareCardData, formatGroupedTokens, shareCardFileName } from '../share-card.js'
import UiIcon from './ui/UiIcon.vue'

const emit = defineEmits(['close'])
const canvasElement = ref(null)
const loading = ref(true)
const error = ref('')
const copyState = ref('')
const downloadState = ref('')
const notice = ref(null)
const data = ref(null)
let canvasBlob = null
let keyHandler

const subtitle = ref('数据统计自今日 00:00 起')

onMounted(async () => {
  keyHandler = event => { if (event.key === 'Escape') emit('close') }
  window.addEventListener('keydown', keyHandler)
  try {
    const [status, uiConfig] = await Promise.all([
      controlFetch('/api/status?usage_window=today'),
      controlFetch('/api/ui-config').catch(() => ({}))
    ])
    data.value = buildShareCardData({
      status: {
        ...status,
        display_name: uiConfig.display_name || status.service,
        brand_mark: uiConfig.brand_mark || 'CX'
      }
    })
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

function drawCard() {
  const canvas = canvasElement.value
  const card = data.value
  if (!canvas || !card) return
  const width = 600
  const height = card.byProvider.length > 2 ? 860 : 780
  const scale = Math.max(2, Math.min(3, Math.round(window.devicePixelRatio || 1) + 1))
  canvas.width = width * scale
  canvas.height = height * scale
  canvas.style.aspectRatio = `${width} / ${height}`
  const context = canvas.getContext('2d')
  context.scale(scale, scale)
  const fontFamily = '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif'

  const background = context.createLinearGradient(0, 0, width, height)
  background.addColorStop(0, '#0f172a')
  background.addColorStop(0.55, '#1e1b4b')
  background.addColorStop(1, '#312e81')
  context.fillStyle = background
  context.fillRect(0, 0, width, height)

  const glow = context.createRadialGradient(width * 0.82, 130, 20, width * 0.82, 130, 260)
  glow.addColorStop(0, 'rgba(94, 234, 212, 0.35)')
  glow.addColorStop(1, 'rgba(94, 234, 212, 0)')
  context.fillStyle = glow
  context.fillRect(0, 0, width, 320)

  context.fillStyle = 'rgba(255, 255, 255, 0.92)'
  roundedRect(context, 36, 92, width - 72, height - 176, 22)
  context.fill()

  context.textBaseline = 'alphabetic'
  context.fillStyle = '#0f172a'
  context.font = `700 22px ${fontFamily}`
  context.fillText(card.displayName, 76, 148)
  context.fillStyle = '#64748b'
  context.font = `400 14px ${fontFamily}`
  context.fillText(`${card.serviceName}`, 76, 172)

  context.fillStyle = '#1e1b4b'
  roundedRect(context, width - 172, 118, 96, 44, 12)
  context.fill()
  context.fillStyle = '#e0f2fe'
  context.font = `700 18px ${fontFamily}`
  context.textAlign = 'center'
  context.fillText(String(card.brandMark).slice(0, 3), width - 124, 147)
  context.textAlign = 'left'

  context.fillStyle = '#64748b'
  context.font = `500 15px ${fontFamily}`
  context.fillText('今日消耗 Token', 76, 232)
  context.fillStyle = '#0f172a'
  context.font = `800 58px ${fontFamily}`
  context.fillText(formatGroupedTokens(card.totalTokens), 76, 296)
  context.fillStyle = '#94a3b8'
  context.font = `400 13px ${fontFamily}`
  const generatedLabel = `截至 ${card.dateLabel} ${card.weekdayLabel} ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
  context.fillText(generatedLabel, 76, 326)

  const metrics = [
    ['输入', card.inputTokens],
    ['输出', card.outputTokens],
    ['缓存', card.cachedTokens],
    ['推理', card.reasoningTokens]
  ]
  const metricTop = 356
  const metricWidth = (width - 152 - 3 * 14) / 4
  metrics.forEach(([label, value], index) => {
    const left = 76 + index * (metricWidth + 14)
    context.fillStyle = '#f1f5f9'
    roundedRect(context, left, metricTop, metricWidth, 66, 12)
    context.fill()
    context.fillStyle = '#64748b'
    context.font = `500 13px ${fontFamily}`
    context.fillText(label, left + 12, metricTop + 24)
    context.fillStyle = '#0f172a'
    context.font = `700 19px ${fontFamily}`
    context.fillText(formatTokenCount(value), left + 12, metricTop + 50)
  })

  const statsTop = metricTop + 100
  context.fillStyle = '#334155'
  context.font = `600 15px ${fontFamily}`
  const rateLabel = card.requestCount > 0 ? `${card.successRate}%` : '—'
  context.fillText(`请求 ${formatGroupedTokens(card.requestCount)} 次 · 成功率 ${rateLabel}${card.failedRequests ? ` · 失败 ${formatGroupedTokens(card.failedRequests)}` : ''}${card.estimatedRequests ? ' · 含估算' : ''}`, 76, statsTop)

  const rankTop = statsTop + 34
  context.fillStyle = '#0f172a'
  context.font = `700 17px ${fontFamily}`
  context.fillText('供应商用量排行', 76, rankTop)
  const listTop = rankTop + 18
  const rowHeight = 58
  card.byProvider.slice(0, 4).forEach((provider, index) => {
    const top = listTop + index * rowHeight
    context.fillStyle = '#e2e8f0'
    context.beginPath()
    context.arc(88, top + 16, 12, 0, Math.PI * 2)
    context.fill()
    context.fillStyle = '#475569'
    context.font = `700 12px ${fontFamily}`
    context.textAlign = 'center'
    context.fillText(String(index + 1), 88, top + 20)
    context.textAlign = 'left'
    context.fillStyle = '#0f172a'
    context.font = `600 15px ${fontFamily}`
    let name = provider.name
    while (name.length > 18 && context.measureText(name).width > 268) name = `${name.slice(0, -2)}…`
    context.fillText(name || provider.providerId, 112, top + 21)
    context.fillStyle = '#0f172a'
    context.font = `700 15px ${fontFamily}`
    context.textAlign = 'right'
    context.fillText(formatTokenCount(provider.totalTokens), width - 76, top + 21)
    context.textAlign = 'left'
    const barLeft = 112
    const barWidth = width - 76 - barLeft
    context.fillStyle = '#e2e8f0'
    roundedRect(context, barLeft, top + 32, barWidth, 8, 4)
    context.fill()
    const maxTokens = card.byProvider[0]?.totalTokens || 1
    const fillWidth = Math.max(6, (provider.totalTokens / maxTokens) * barWidth)
    const bar = context.createLinearGradient(barLeft, 0, barLeft + barWidth, 0)
    bar.addColorStop(0, '#2dd4bf')
    bar.addColorStop(1, '#6366f1')
    context.fillStyle = bar
    roundedRect(context, barLeft, top + 32, fillWidth, 8, 4)
    context.fill()
    context.fillStyle = '#94a3b8'
    context.font = `400 12px ${fontFamily}`
    context.fillText(`${provider.tokenShare}% · ${formatGroupedTokens(provider.requestCount)} 次`, 112, top + 54)
  })
  if (!card.byProvider.length) {
    context.fillStyle = '#94a3b8'
    context.font = `500 14px ${fontFamily}`
    context.fillText('今天还没有供应商用量', 76, listTop + 24)
  }

  context.fillStyle = 'rgba(226, 232, 240, 0.9)'
  context.font = `500 13px ${fontFamily}`
  context.textAlign = 'center'
  context.fillText('Codex Provider Hub · 本地中转统计', width / 2, height - 118)
  context.fillStyle = 'rgba(148, 163, 184, 0.85)'
  context.font = `400 12px ${fontFamily}`
  context.fillText('数据仅保存在本机，卡片由控制台生成', width / 2, height - 96)
  context.textAlign = 'left'

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
  if (!blob) { notice.value = { detail: '画布不可用，请重新打开卡片窗口', tone: 'error' }; return }
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
    notice.value = { detail: '卡片已复制，可以直接粘贴到聊天或文档', tone: 'success' }
    window.setTimeout(() => { copyState.value = '' }, 2200)
  } catch (copyError) {
    copyState.value = ''
    notice.value = {
      detail: copyError.message === 'clipboard-unsupported' ? '当前浏览器不支持复制图片，请使用下载图片。' : '复制失败，请使用下载图片。',
      tone: 'error'
    }
  }
}
</script>
