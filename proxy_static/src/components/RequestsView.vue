<template>
  <section id="requests-view" class="requests-view view-panel" aria-labelledby="requests-title">
    <div class="requests-heading">
      <div><h2 id="requests-title">请求记录</h2><p>{{ error || `${totalCount} 条记录 · 运行中 ${activeItems.length} 条 · 最多保留 7 天` }}</p></div>
      <div class="requests-heading-actions"><button v-if="config.features?.session_routing" class="icon-button" type="button" title="设置会话路由" aria-label="设置会话路由" @click="openSessionRoutes"><UiIcon name="settings" /></button><button class="icon-button" type="button" title="刷新请求记录" aria-label="刷新请求记录" @click="loadRequests"><UiIcon name="refresh" /></button></div>
    </div>
    <div class="request-filters" aria-label="请求筛选">
      <label class="request-search"><UiIcon class="search-icon" name="search" /><input v-model="query" type="search" placeholder="搜索会话、模型或错误" autocomplete="off" @input="scheduleSearch" /></label>
      <div class="time-window-control request-window-control"><UiSelect v-model="windowName" aria-label="时间范围" :options="requestWindowOptions" @change="loadRequests" /><button class="time-range-edit" type="button" title="设置自定义请求时间" aria-label="设置自定义请求时间" @click="openCustomRange"><UiIcon name="calendar" /></button></div>
      <UiSelect v-model="statusFilter" aria-label="请求状态" :options="statusOptions" @change="loadRequests" />
      <UiSelect v-model="providerFilter" aria-label="供应商" :options="providerOptions" @change="loadRequests" />
    </div>
    <div class="request-table-shell">
      <div class="request-table-header" aria-hidden="true"><span class="request-column-status">状态</span><span class="request-column-time">开始时间</span><span>会话</span><span>供应商</span><span>模型</span><span class="request-column-reasoning">推理强度</span><span class="request-column-duration">耗时</span><span class="request-column-token">Token</span><span class="request-column-result">结果</span></div>
      <div class="request-list" aria-live="polite">
        <div v-for="(item, index) in combinedItems" :key="requestKey(item, index)" class="request-row" :class="requestState(item)">
          <span class="request-state"><span class="request-state-dot" aria-hidden="true" /><span>{{ stateLabel(item) }}</span></span>
          <time class="request-time" :datetime="isoTime(item.started_at)">{{ formatTime(item.started_at) }}</time>
          <strong class="request-session" :title="item.session_name || '未知会话'">{{ item.session_name || '未知会话' }}</strong>
          <span class="request-provider-cell"><strong>{{ providerName(item.provider_id || item.actual_provider_id) }}</strong><small v-if="item.requested_provider_id && item.requested_provider_id !== item.provider_id">请求 {{ providerName(item.requested_provider_id) }}</small></span>
          <span class="request-model" :title="item.model || 'unknown'">{{ item.model || 'unknown' }}</span>
          <span class="request-reasoning">{{ reasoningLabel(item.reasoning_effort) }}</span>
          <span class="request-duration">{{ durationLabel(item) }}</span>
          <span class="request-token">{{ item.usage_source == null && !item.total_tokens ? '—' : formatNumber(item.total_tokens) }}</span>
          <span class="request-result" :title="item.error_summary || ''">{{ resultLabel(item) }}</span>
        </div>
      </div>
      <div v-if="!combinedItems.length" class="requests-empty">{{ loading ? '正在读取请求记录…' : (error || '当前筛选范围内没有请求记录') }}</div>
      <button v-if="nextCursor" class="usage-history-more" type="button" @click="loadMore">加载更早记录</button>
    </div>

    <TimeRangePopover :open="customRangeOpen" title="自定义请求时间" :initial-range="customRange" @close="closeCustomRange" @apply="applyCustomRange" />

    <div v-if="routePopover" class="session-route-popover" style="top: 120px; left: max(12px, calc(50vw - 190px));">
      <div class="session-route-heading"><strong>设置会话路由</strong><button class="usage-history-close" type="button" aria-label="关闭会话路由设置" @click="routePopover = false"><UiIcon name="close" /></button></div>
      <label class="session-route-field"><span>会话</span><UiSelect v-model="selectedSession" aria-label="会话" :options="sessionOptions" /></label>
      <label class="session-route-field"><span>后续供应商</span><UiSelect v-model="selectedRoute" aria-label="后续供应商" :disabled="!selectedSession" :options="providerOptionsWithDefault" @change="saveSessionRoute" /></label>
      <p class="session-route-meta">只显示最近 7 天的 Codex 会话，活跃会话优先。</p>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
import TimeRangePopover from './TimeRangePopover.vue'
import UiSelect from './ui/UiSelect.vue'
import UiIcon from './ui/UiIcon.vue'
defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['count'])
const activeItems = ref([])
const historyItems = ref([])
const providers = ref([])
const totalCount = ref(0)
const nextCursor = ref(null)
const windowName = ref(localStorage.getItem('local-proxy-request-window') || '24h')
const customRangeOpen = ref(false)
const customRange = ref(null)
const statusFilter = ref('all')
const providerFilter = ref('')
const query = ref('')
const loading = ref(false)
const error = ref('')
const routePopover = ref(false)
const sessions = ref([])
const selectedSession = ref('')
const selectedRoute = ref('')
let timer
let searchTimer
const requestWindowOptions = [{ value: '1h', label: '近 1 小时' }, { value: '6h', label: '近 6 小时' }, { value: '24h', label: '近 24 小时' }, { value: '7d', label: '近 7 天' }, { value: 'custom', label: '自定义时间…' }]
const statusOptions = [{ value: 'all', label: '全部状态' }, { value: 'running', label: '运行中' }, { value: 'succeeded', label: '成功' }, { value: 'failed', label: '失败' }]
const combinedItems = computed(() => [...activeItems.value, ...historyItems.value])
const providerNames = computed(() => new Map(providers.value.map(provider => [provider.provider_id, provider.name])))
const providerOptions = computed(() => [{ value: '', label: '全部供应商' }, ...providers.value.map(provider => ({ value: provider.provider_id, label: provider.name }))])
const providerOptionsWithDefault = computed(() => [{ value: '', label: '跟随全局路由' }, ...providers.value.map(provider => ({ value: provider.provider_id, label: provider.name }))])
const sessionOptions = computed(() => [{ value: '', label: '请选择会话' }, ...sessions.value.map(session => ({ value: session.session_key, label: session.session_name || session.thread_name || session.session_key }))])
function providerName(id) { return providerNames.value.get(id) || id || '—' }
function requestKey(item, index) { return `${item.request_id || item.started_at || 0}-${item.provider_id || 'unknown'}-${index}` }
function requestState(item) { return item.state === 'running' ? 'running' : item.error_kind === 'client_disconnected' ? 'cancelled' : item.succeeded === true ? 'succeeded' : 'failed' }
function stateLabel(item) { return ({ running: '运行中', succeeded: '成功', cancelled: '取消', failed: '失败' })[requestState(item)] }
function runningResultLabel(item) { if (item.outcome === 'retrying' || item.phase === 'retrying') return `第 ${Number(item.retry_count || 0) + 1} 次尝试`; return ({ connecting: '连接上游', waiting_first_chunk: '等待首包', receiving: '接收中' })[item.phase] || (item.outcome === 'receiving' ? '接收中' : '处理中') }
function retryErrorLabel(item) { return ({ connection_timeout: '等待上游响应超时', stream_idle_timeout: '上游长时间无数据' })[item.error_kind] || '' }
function formatNumber(value) { return Number(value || 0).toLocaleString('zh-CN') }
function timestamp(value) { const number = Number(value || 0); return number < 10_000_000_000 ? number * 1000 : number }
function isoTime(value) { try { return new Date(timestamp(value)).toISOString() } catch { return '' } }
function formatTime(value) { return value ? new Date(timestamp(value)).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function reasoningLabel(value) { return ({ low: '低', medium: '中', high: '高', xhigh: '超高' })[value] || '—' }
function durationLabel(item) { const value = requestState(item) === 'running' ? Date.now() - timestamp(item.started_at) : item.duration_ms; if (value == null) return '—'; return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms` }
function resultLabel(item) { if (requestState(item) === 'running') return runningResultLabel(item); if (item.succeeded) return item.status_code ? `HTTP ${item.status_code}` : '已完成'; if (item.error_kind === 'client_disconnected') return '客户端断开'; return item.error_summary || retryErrorLabel(item) || item.error_kind || (item.status_code ? `HTTP ${item.status_code}` : '失败') }
function scheduleSearch() { window.clearTimeout(searchTimer); searchTimer = window.setTimeout(loadRequests, 250) }
function requestParams(includeCursor = false) {
  const params = new URLSearchParams({ window: windowName.value, status: statusFilter.value })
  if (windowName.value === 'custom' && customRange.value) {
    params.set('start_at', String(customRange.value.startAt))
    params.set('end_at', String(customRange.value.endAt))
  }
  if (includeCursor && nextCursor.value) params.set('cursor', nextCursor.value)
  if (providerFilter.value) params.set('provider_id', providerFilter.value)
  if (query.value.trim()) params.set('query', query.value.trim())
  return params
}
async function loadRequests() {
  if (loading.value) return
  if (windowName.value === 'custom' && !customRange.value) { customRangeOpen.value = true; return }
  loading.value = true; error.value = ''; localStorage.setItem('local-proxy-request-window', windowName.value)
  try {
    const params = requestParams()
    const [payload, status] = await Promise.all([controlFetch(`/api/requests?${params}`), controlFetch('/api/status')])
    activeItems.value = payload.active || []; historyItems.value = payload.items || []; totalCount.value = Number(payload.total_count || 0); nextCursor.value = payload.next_cursor || null; providers.value = status.providers || []; emit('count', Number(status.active_requests || 0))
  } catch (requestError) { error.value = requestError.message } finally { loading.value = false }
}
async function loadMore() { if (!nextCursor.value) return; const payload = await controlFetch(`/api/requests?${requestParams(true)}`); historyItems.value.push(...(payload.items || [])); nextCursor.value = payload.next_cursor || null }
function openCustomRange() { customRangeOpen.value = true }
function closeCustomRange() { customRangeOpen.value = false; if (!customRange.value && windowName.value === 'custom') windowName.value = '24h' }
function applyCustomRange(range) { customRange.value = range; windowName.value = 'custom'; customRangeOpen.value = false; loadRequests() }
async function openSessionRoutes() { routePopover.value = true; try { const payload = await controlFetch('/api/sessions'); sessions.value = Array.isArray(payload) ? payload : (payload.items || payload.sessions || []) } catch (requestError) { error.value = requestError.message } }
async function saveSessionRoute() { if (!selectedSession.value) return; await controlFetch(`/api/session-routes/${encodeURIComponent(selectedSession.value)}`, jsonOptions('POST', { provider_id: selectedRoute.value || null })) }
onMounted(() => { loadRequests(); timer = window.setInterval(loadRequests, 5000) })
onBeforeUnmount(() => { window.clearInterval(timer); window.clearTimeout(searchTimer) })
</script>
