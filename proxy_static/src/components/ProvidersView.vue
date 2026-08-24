<template>
  <div id="providers-view" class="workspace view-panel">
    <section class="provider-pane" aria-labelledby="providers-title">
      <div class="toolbar">
        <div class="toolbar-copy">
          <h2 id="providers-title">选择转发供应商</h2>
          <p class="provider-count">{{ loading ? '正在读取 CC Switch...' : (manageMode ? `共 ${providers.length} 个供应商，已隐藏 ${hiddenCount} 个` : `已显示 ${visibleProviders.length} 个 ${uiConfig.provider_label || ''} 供应商`) }}<button class="health-refresh-button" type="button" title="刷新服务器检测数据" aria-label="刷新服务器检测数据" @click="loadHealth"><UiIcon name="refresh" /></button></p>
        </div>
        <div class="toolbar-actions">
          <div class="time-window-control usage-window-wrap">
            <UiSelect v-model="usageWindow" class="usage-window-control" aria-label="Token 统计时间范围" icon="clock" :options="usageWindowOptions" @change="loadStatus" />
            <button class="time-range-edit" type="button" title="设置自定义 Token 时间" aria-label="设置自定义 Token 时间" @click="customRangeOpen = true"><UiIcon name="calendar" /></button>
          </div>
          <label class="search"><span class="visually-hidden">搜索供应商</span><UiIcon class="search-icon" name="search" /><input v-model="query" type="search" placeholder="搜索名称或地址" autocomplete="off" /></label>
          <button v-if="catalogEnabled" class="secondary-button" type="button" :aria-pressed="manageMode" @click="manageMode = !manageMode">{{ manageMode ? '完成' : '管理' }}</button>
          <button class="icon-button" type="button" title="重新读取 CC Switch" aria-label="重新读取 CC Switch" @click="loadStatus"><UiIcon name="refresh" /></button>
        </div>
        <div v-if="manageMode" class="provider-management-bar">
          <div class="provider-management-heading"><strong>供应商管理</strong><span>本地目录</span></div>
          <div class="provider-catalog-controls">
            <button class="secondary-button provider-add-button" type="button" @click="openCreate"><UiIcon name="plus" />新增</button>
            <div class="provider-import-actions">
              <label class="provider-import-mode"><span>导入方式</span><UiSelect v-model="importMode" aria-label="CCS 导入方式" :options="importModeOptions" /></label>
              <button class="secondary-button" type="button" @click="importProviders">导入 CCS</button>
            </div>
          </div>
        </div>
      </div>

      <div class="usage-summary" aria-live="polite">
        <div class="usage-metric total"><span>Token 总量</span><strong>{{ formatNumber(usage.total_tokens) }}</strong></div>
        <div class="usage-metric"><span>输入</span><strong>{{ formatNumber(usage.input_tokens) }}</strong></div>
        <div class="usage-metric"><span>输出</span><strong>{{ formatNumber(usage.output_tokens) }}</strong></div>
        <div class="usage-metric"><span>缓存</span><strong>{{ formatNumber(usage.cached_tokens) }}</strong></div>
        <div class="usage-estimate-wrap"><span v-if="usage.estimated_requests" id="usage-estimated-note">含估算请求</span></div>
      </div>

      <div class="provider-list-shell">
        <div v-if="manageMode" class="manage-hint">拖动左侧手柄调整顺序；搜索时暂停拖动。隐藏的供应商仅在管理模式显示。</div>
        <div class="list-header" aria-hidden="true"><span>状态</span><span>名称和地址</span><span>服务器检测</span><span>Token 用量</span><span>请求次数</span><span>{{ manageMode ? '管理' : '认证' }}</span></div>
        <div v-if="filteredProviders.length" class="provider-list" aria-live="polite">
          <article v-for="provider in filteredProviders" :key="provider.provider_id" class="provider-row" :class="{ current: provider.current, managing: manageMode, 'hidden-provider': provider.hidden, dragging: draggedId === provider.provider_id }" :draggable="manageMode && !query" @dragstart="startDrag(provider)" @dragend="draggedId = ''" @dragover.prevent @drop.prevent="dropProvider(provider)" @click="!manageMode && !provider.current && selectProvider(provider)">
            <div class="provider-state"><UiIcon v-if="manageMode" name="grip" class="drag-handle" /><span class="dot" /><span>{{ provider.current ? '当前' : (provider.hidden ? '隐藏' : '可用') }}</span></div>
            <div class="provider-copy">
              <div class="provider-title">
                <button class="provider-select" type="button" :disabled="provider.current || manageMode || switchingId === provider.provider_id" @click.stop="selectProvider(provider)"><strong>{{ provider.name }}</strong></button>
                <button v-if="showLaunchCommand && !manageMode" class="copy-command" type="button" @click.stop="copyLaunchCommand(provider)">复制临时启动命令</button>
                <button v-if="provider.status_upload_supported && !manageMode" class="copy-command status-upload-button" type="button" @click.stop="openStatusUpload(provider)">上传检测</button>
              </div>
              <code>{{ provider.endpoint || '未提供地址' }}</code>
            </div>
            <div class="provider-health-cell">
              <div v-if="healthFor(provider)" class="provider-health-summary">
                <div class="provider-health-top"><span class="provider-health-label"><span class="provider-health-dot" :class="healthState(provider)" />{{ healthLabel(provider) }}</span><span class="provider-health-latency">{{ healthLatency(provider) }}</span></div>
                <div class="provider-health-meta"><span>{{ healthAvailability(provider) }}</span><span>{{ healthTime(provider) }}</span></div>
                <div class="provider-health-history"><span v-for="index in 12" :key="index" class="provider-health-mark" :class="healthState(provider)" /></div>
              </div>
              <span v-else class="provider-request-empty">暂无数据</span>
              <button v-if="healthFor(provider)" class="provider-health-toggle" type="button" title="查看检测详情" @click.stop="selectedHealth = provider">详情</button>
            </div>
            <button class="provider-token-cell" type="button" :disabled="!providerUsage(provider.provider_id).request_count" @click.stop="selectedUsage = provider">
              <strong :class="providerUsage(provider.provider_id).total_tokens ? 'provider-token-value' : 'provider-token-zero'">{{ formatNumber(providerUsage(provider.provider_id).total_tokens) }}</strong>
              <span v-if="providerUsage(provider.provider_id).request_count" class="provider-token-detail-icon">i</span>
            </button>
            <div class="provider-request-cell"><button v-if="provider.active_requests" class="active-badge" type="button" :title="sessionNames(provider)">{{ provider.active_requests }} 活动</button><span class="provider-request-count">{{ providerUsage(provider.provider_id).request_count || 0 }}</span></div>
            <div class="provider-meta">
              <span class="auth-label" :class="{ missing: !provider.has_credentials }">{{ provider.has_credentials ? '已配置' : '缺少 Key' }}</span>
              <div v-if="manageMode" class="provider-row-actions">
                <button class="provider-action-button visibility-button" type="button" :disabled="provider.current" @click.stop="setProviderHidden(provider, !provider.hidden)">{{ provider.hidden ? '显示' : '隐藏' }}</button>
                <button class="provider-action-button provider-edit-button" type="button" @click.stop="openEdit(provider)">编辑</button>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">{{ loading ? '正在读取供应商...' : '没有匹配的供应商' }}</div>
      </div>
    </section>

    <aside class="routing-panel" aria-labelledby="routing-title">
      <div class="routing-heading"><h2 id="routing-title">当前路由</h2></div>
      <div class="route-provider"><strong>{{ currentProvider?.name || '正在读取' }}</strong><code>{{ currentProvider?.endpoint || '—' }}</code></div>
      <div class="metric-list">
        <div class="metric"><span>活动请求</span><strong>{{ status.active_requests || 0 }}</strong></div>
        <div class="metric"><span>上次请求</span><strong>{{ lastRequestLabel }}</strong></div>
        <div class="metric"><span>认证信息</span><strong>{{ currentProvider?.has_credentials ? '已安全读取' : '缺失' }}</strong></div>
        <div class="metric"><span>请求协议</span><strong>{{ wireApiLabel }}</strong></div>
        <div class="metric"><span>自动恢复</span><strong>{{ status.retry?.enabled === false ? '已关闭' : '已启用' }}</strong></div>
      </div>
      <div class="recovery" :class="{ active: status.retry?.active?.length, blocked: status.retry?.circuit_open?.length }">
        <div class="recovery-heading"><strong>{{ recoveryTitle }}</strong><button v-if="status.retry?.recent_errors?.length" class="recovery-details-button" type="button" aria-label="查看最近 24 小时恢复记录">i</button></div>
      </div>
      <div class="draining"><strong>{{ drainingRequests ? `${drainingRequests} 个旧请求正在处理` : '没有旧请求正在处理' }}</strong></div>
    </aside>

    <dialog class="provider-editor" :open="editorOpen" aria-labelledby="provider-editor-title">
      <form method="dialog" @submit.prevent="saveProvider">
        <div class="provider-editor-heading"><div><strong id="provider-editor-title">{{ editingId ? '编辑供应商' : '供应商' }}</strong><span>{{ editingId ? '修改本地供应商' : '新增本地供应商' }}</span></div><button class="usage-history-close" type="button" aria-label="关闭供应商编辑" @click="closeEditor"><UiIcon name="close" /></button></div>
        <label class="provider-editor-field"><span>名称</span><input v-model.trim="form.name" type="text" maxlength="240" required autocomplete="off" /></label>
        <label class="provider-editor-field"><span>Base URL</span><input v-model.trim="form.base_url" type="url" required spellcheck="false" autocomplete="off" placeholder="https://api.example.com/v1" /></label>
        <label class="provider-editor-field"><span>Wire API</span><UiSelect v-model="form.wire_api" aria-label="Wire API" :options="wireApiOptions" /></label>
        <label class="provider-editor-field"><span>请求传输方式</span><UiSelect v-model="form.transport" aria-label="请求传输方式" :options="transportOptions" /></label>
        <label class="provider-editor-field"><span>API Key</span><input v-model="form.api_key" type="password" autocomplete="new-password" placeholder="新增时填写，编辑时留空表示保留" /></label>
        <label v-if="editingId" class="provider-editor-check"><input v-model="form.clear_api_key" type="checkbox" /><span>清除已保存的 API Key</span></label>
        <label class="provider-editor-field"><span>HTTP Headers <small>JSON 对象</small></span><textarea v-model="form.headers" rows="3" spellcheck="false" /></label>
        <label class="provider-editor-field"><span>Query Params <small>JSON 对象</small></span><textarea v-model="form.query_params" rows="3" spellcheck="false" /></label>
        <p v-if="formError" class="provider-editor-error" role="alert">{{ formError }}</p>
        <div class="provider-editor-actions"><button v-if="editingId" class="danger-button" type="button" :disabled="editingId === currentProvider?.provider_id" @click="deleteProvider">删除供应商</button><span v-else /><div class="provider-editor-primary-actions"><button class="secondary-button" type="button" @click="closeEditor">取消</button><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button></div></div>
      </form>
    </dialog>

    <div v-if="statusUploadProvider" class="status-upload-modal" role="dialog" aria-modal="true" aria-labelledby="status-upload-title" @click.self="statusUploadProvider = null">
      <div class="status-upload-dialog">
        <div class="status-upload-heading"><div><strong id="status-upload-title">上传到服务器检测</strong><span>{{ statusUploadProvider.name }}</span></div><button class="usage-history-close" type="button" aria-label="关闭上传窗口" @click="statusUploadProvider = null"><UiIcon name="close" /></button></div>
        <p class="status-upload-help">选择要检测的模型。API Key 或 Auth Token 只会通过受限 SSH 命令发送到服务器，不会显示在页面。</p>
        <div class="status-upload-models"><div v-for="(_, index) in uploadModels" :key="index" class="status-upload-model-row"><input v-model="uploadModels[index]" class="status-upload-model-input" placeholder="模型名称" /><button class="icon-button status-upload-remove" type="button" aria-label="移除模型" @click="uploadModels.splice(index, 1)"><UiIcon name="close" /></button></div></div>
        <button class="secondary-button" type="button" @click="uploadModels.push('')">添加模型</button>
        <button v-if="uploadInitialized && !showUploadSsh" class="text-button" type="button" @click="showUploadSsh = true">更换服务器</button>
        <div v-if="showUploadSsh" class="status-upload-ssh"><strong>首次连接服务器</strong><label>服务器地址<input v-model.trim="uploadSsh.host" type="text" autocomplete="off" /></label><div class="status-upload-inline"><label>SSH 端口<input v-model.number="uploadSsh.port" type="number" min="1" max="65535" /></label><label>用户名<input v-model.trim="uploadSsh.username" type="text" autocomplete="username" /></label></div><label>服务器密码<input v-model="uploadSsh.password" type="password" autocomplete="current-password" /></label></div>
        <p v-if="uploadError" class="status-upload-error" role="alert">{{ uploadError }}</p>
        <div class="status-upload-actions"><button class="secondary-button" type="button" @click="statusUploadProvider = null">取消</button><button class="primary-button" type="button" :disabled="uploadSaving" @click="uploadStatus">{{ uploadSaving ? '上传中...' : '上传' }}</button></div>
      </div>
    </div>

    <div v-if="selectedHealth" class="provider-health-popover show" style="top: 96px; left: max(12px, calc(50vw - 250px));"><div class="provider-health-detail"><div class="provider-health-detail-head"><div><strong>{{ selectedHealth.name }}</strong><span>服务器检测详情</span></div><button class="provider-health-close" type="button" aria-label="关闭检测详情" @click="selectedHealth = null"><UiIcon name="close" /></button></div><div class="provider-health-metrics"><span><span>状态</span><strong>{{ healthLabel(selectedHealth) }}</strong></span><span><span>可用率</span><strong>{{ healthAvailability(selectedHealth) }}</strong></span><span><span>延迟</span><strong>{{ healthLatency(selectedHealth) }}</strong></span></div></div></div>
    <div v-if="selectedUsage" class="usage-history-popover show" style="top: 96px; left: max(12px, calc(50vw - 240px));"><div class="usage-history-heading"><div><strong>{{ selectedUsage.name }} 请求记录</strong><span>{{ usageWindowLabel }}</span></div><button class="usage-history-close" type="button" aria-label="关闭用量记录" @click="selectedUsage = null"><UiIcon name="close" /></button></div><div class="usage-history-summary"><span>Token {{ formatNumber(providerUsage(selectedUsage.provider_id).total_tokens) }}</span><span>请求 {{ providerUsage(selectedUsage.provider_id).request_count || 0 }}</span></div><div class="usage-history-empty">详细请求请在“请求”页面查看</div></div>
    <TimeRangePopover :open="customRangeOpen" title="自定义 Token 时间" :initial-range="customRange" @close="closeCustomRange" @apply="applyCustomRange" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
import TimeRangePopover from './TimeRangePopover.vue'
import UiSelect from './ui/UiSelect.vue'
import UiIcon from './ui/UiIcon.vue'

const status = ref({ providers: [], usage: { total: {}, by_provider: {} }, retry: {} })
const uiConfig = ref({ features: {} })
const health = ref(null)
const healthError = ref('')
const loading = ref(true)
const query = ref('')
const usageWindow = ref(localStorage.getItem('local-proxy-usage-window') || 'today')
const usageWindowOptions = [
  { value: 'today', label: '今日' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'all', label: '全部' },
  { value: 'custom', label: '自定义时间…' }
]
const importModeOptions = [{ value: 'skip', label: '仅新增' }, { value: 'overwrite', label: '覆盖已有' }]
const wireApiOptions = [{ value: 'responses', label: 'responses' }]
const transportOptions = [{ value: 'httpx', label: '标准模式（httpx）' }, { value: 'curl_cffi', label: '兼容模式（curl_cffi）' }]
const manageMode = ref(false)
const importMode = ref('skip')
const switchingId = ref('')
const draggedId = ref('')
const editorOpen = ref(false)
const editingId = ref('')
const saving = ref(false)
const formError = ref('')
const statusUploadProvider = ref(null)
const uploadModels = ref(['gpt-5'])
const uploadInitialized = ref(false)
const showUploadSsh = ref(false)
const uploadSaving = ref(false)
const uploadError = ref('')
const uploadSsh = ref({ host: '118.195.178.173', port: 22, username: 'ubuntu', password: '' })
const selectedHealth = ref(null)
const selectedUsage = ref(null)
const customRangeOpen = ref(false)
const customRange = ref(null)
let timer

const emptyForm = () => ({ name: '', base_url: '', api_key: '', clear_api_key: false, transport: 'httpx', wire_api: 'responses', headers: '{}', query_params: '{}' })
const form = ref(emptyForm())
const providers = computed(() => status.value.providers || [])
const catalogEnabled = computed(() => uiConfig.value.features?.provider_catalog === true)
const showLaunchCommand = computed(() => uiConfig.value.features?.provider_launch_command !== false)
const visibleProviders = computed(() => providers.value.filter(provider => manageMode.value || !provider.hidden))
const hiddenCount = computed(() => providers.value.filter(provider => provider.hidden).length)
const filteredProviders = computed(() => { const term = query.value.trim().toLocaleLowerCase(); return visibleProviders.value.filter(provider => !term || [provider.name, provider.endpoint].some(value => String(value || '').toLocaleLowerCase().includes(term))) })
const currentProvider = computed(() => providers.value.find(provider => provider.current || provider.provider_id === status.value.current_provider_id))
const usage = computed(() => status.value.usage?.total || {})
const drainingRequests = computed(() => providers.value.reduce((sum, provider) => sum + Number(provider.draining_requests || 0), 0))
const lastRequestLabel = computed(() => status.value.last_status_code ? `HTTP ${status.value.last_status_code}` : '尚无请求')
const recoveryTitle = computed(() => status.value.retry?.circuit_open?.length ? '自动恢复已暂停' : (status.value.retry?.active?.length ? '正在自动恢复' : '自动恢复已就绪'))
const wireApiLabel = computed(() => currentProvider.value?.wire_api === 'responses' ? 'Responses · SSE' : currentProvider.value?.wire_api === 'anthropic_messages' ? 'Messages · SSE' : (currentProvider.value?.wire_api || uiConfig.value.protocol_label || '—'))
const usageWindowLabel = computed(() => ({ today: '今日', '24h': '近 24 小时', '7d': '近 7 天', '30d': '近 30 天', all: '全部' })[usageWindow.value])

function formatNumber(value) { return Number(value || 0).toLocaleString('zh-CN') }
function providerUsage(id) { return status.value.usage?.by_provider?.[id] || {} }
function sessionNames(provider) { return (provider.active_sessions || []).map(item => item.name || item.session_name || item).join('\n') }
function normalizeEndpoint(value) { return String(value || '').replace(/^https?:\/\//, '').replace(/\/$/, '').toLowerCase() }
function healthFor(provider) { return health.value?.providers?.find(item => normalizeEndpoint(item.endpoint || item.base_url) === normalizeEndpoint(provider.endpoint) || item.id === provider.provider_id || item.name === provider.name) }
function healthState(provider) { const value = String(healthFor(provider)?.state || 'unknown').toLowerCase(); return value === 'up' ? 'healthy' : value === 'degraded' ? 'warning' : value === 'down' ? 'down' : value }
function healthLabel(provider) { return ({ healthy: '正常', warning: '波动', down: '不可用', unknown: '未知' })[healthState(provider)] || healthState(provider) }
function healthLatency(provider) { const value = healthFor(provider)?.latency_ms ?? healthFor(provider)?.latency; return Number.isFinite(Number(value)) ? `${Math.round(Number(value))} ms` : '—' }
function healthAvailability(provider) { const value = healthFor(provider)?.availability ?? healthFor(provider)?.uptime; return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : '—' }
function healthTime(provider) { const value = healthFor(provider)?.checked_at || healthFor(provider)?.updated_at; return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }

async function loadStatus() {
  if (loading.value && providers.value.length) return
  try {
    localStorage.setItem('local-proxy-usage-window', usageWindow.value)
    if (usageWindow.value === 'custom' && !customRange.value) { customRangeOpen.value = true; return }
    const queryParams = usageWindow.value === 'custom'
      ? `start_at=${customRange.value.startAt}&end_at=${customRange.value.endAt}`
      : `usage_window=${encodeURIComponent(usageWindow.value)}`
    const [payload, config] = await Promise.all([controlFetch(`/api/status?${queryParams}`), controlFetch('/api/ui-config')])
    status.value = payload
    uiConfig.value = config
    if (!health.value && payload.health_status_url) loadHealth()
  } finally { loading.value = false }
}
async function loadHealth() { const url = status.value.health_status_url; if (!url) return; healthError.value = ''; try { const response = await fetch(url, { cache: 'no-store', mode: 'cors' }); if (!response.ok) throw new Error(`HTTP ${response.status}`); health.value = await response.json() } catch (error) { healthError.value = error.message } }
async function selectProvider(provider) { if (provider.current || switchingId.value) return; switchingId.value = provider.provider_id; try { status.value = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}/select?usage_window=${encodeURIComponent(usageWindow.value)}`, { method: 'POST' }) } finally { switchingId.value = '' } }
async function setProviderHidden(provider, hidden) { status.value = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}/visibility?usage_window=${encodeURIComponent(usageWindow.value)}`, jsonOptions('POST', { hidden })) }
function startDrag(provider) { if (manageMode.value && !query.value) draggedId.value = provider.provider_id }
async function dropProvider(target) {
  if (!draggedId.value || draggedId.value === target.provider_id) return
  const ordered = providers.value.slice()
  const from = ordered.findIndex(item => item.provider_id === draggedId.value)
  const to = ordered.findIndex(item => item.provider_id === target.provider_id)
  const [moved] = ordered.splice(from, 1)
  ordered.splice(to, 0, moved)
  draggedId.value = ''
  status.value = await controlFetch(`/api/providers/order?usage_window=${encodeURIComponent(usageWindow.value)}`, jsonOptions('POST', { provider_ids: ordered.map(item => item.provider_id) }))
}
async function importProviders() { await controlFetch('/api/providers/import/cc-switch', jsonOptions('POST', { overwrite: importMode.value === 'overwrite' })); await loadStatus() }
function openCreate() { editingId.value = ''; form.value = emptyForm(); formError.value = ''; editorOpen.value = true }
async function openEdit(provider) { const detail = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}`); editingId.value = provider.provider_id; form.value = { name: detail.name || '', base_url: detail.base_url || '', api_key: '', clear_api_key: false, transport: detail.transport || 'httpx', wire_api: detail.wire_api || 'responses', headers: JSON.stringify(detail.headers || {}, null, 2), query_params: JSON.stringify(detail.query_params || {}, null, 2) }; editorOpen.value = true }
function closeEditor() { if (!saving.value) editorOpen.value = false }
function parseObject(value, label) { const parsed = JSON.parse(value || '{}'); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label} 必须是 JSON 对象`); return parsed }
async function saveProvider() { saving.value = true; formError.value = ''; try { const payload = { name: form.value.name, base_url: form.value.base_url, api_key: form.value.api_key || null, clear_api_key: form.value.clear_api_key, transport: form.value.transport, wire_api: form.value.wire_api, headers: parseObject(form.value.headers, 'Headers'), query_params: parseObject(form.value.query_params, 'Query Params') }; await controlFetch(editingId.value ? `/api/providers/${encodeURIComponent(editingId.value)}` : '/api/providers', jsonOptions(editingId.value ? 'PUT' : 'POST', payload)); editorOpen.value = false; await loadStatus() } catch (error) { formError.value = error.message } finally { saving.value = false } }
async function deleteProvider() { await controlFetch(`/api/providers/${encodeURIComponent(editingId.value)}`, { method: 'DELETE' }); editorOpen.value = false; await loadStatus() }
async function copyLaunchCommand(provider) { const payload = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}/launch-command`, { method: 'POST' }); await navigator.clipboard.writeText(payload.command || payload.content || String(payload)) }
async function openStatusUpload(provider) {
  statusUploadProvider.value = provider
  uploadError.value = ''
  uploadSaving.value = false
  try {
    const [settings, preview] = await Promise.all([
      controlFetch('/api/status-upload/settings').catch(() => ({})),
      controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}/status-upload/preview`, jsonOptions('POST', { models: [] }))
    ])
    if (preview.supported !== true) throw new Error(preview.detail || preview.reason || '该供应商不支持上传')
    uploadModels.value = preview.suggested_models?.length ? [...preview.suggested_models] : ['gpt-5']
    uploadInitialized.value = settings.initialized === true
    showUploadSsh.value = !uploadInitialized.value
    uploadSsh.value = { host: settings.host || '118.195.178.173', port: Number(settings.port || 22), username: settings.username || 'ubuntu', password: '' }
  } catch (error) { uploadError.value = error.message }
}
async function uploadStatus() {
  const models = uploadModels.value.map(value => value.trim()).filter(Boolean)
  if (!models.length) { uploadError.value = '至少填写一个检测模型'; return }
  uploadSaving.value = true; uploadError.value = ''
  try {
    if (showUploadSsh.value) {
      await controlFetch('/api/status-upload/bootstrap', jsonOptions('POST', { host: uploadSsh.value.host, port: Number(uploadSsh.value.port), username: uploadSsh.value.username, password: uploadSsh.value.password }))
      uploadInitialized.value = true; showUploadSsh.value = false
    }
    await controlFetch(`/api/providers/${encodeURIComponent(statusUploadProvider.value.provider_id)}/status-upload`, jsonOptions('POST', { models }))
    statusUploadProvider.value = null
  } catch (error) { uploadError.value = error.message } finally { uploadSaving.value = false }
}
function closeCustomRange() { customRangeOpen.value = false; if (!customRange.value && usageWindow.value === 'custom') usageWindow.value = 'today' }
function applyCustomRange(range) { customRange.value = range; usageWindow.value = 'custom'; customRangeOpen.value = false; loadStatus() }

onMounted(() => { loadStatus(); timer = window.setInterval(() => loadStatus(), 5000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>
