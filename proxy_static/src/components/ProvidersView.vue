<template>
  <div id="providers-view" class="workspace view-panel">
    <section class="provider-pane" aria-labelledby="providers-title">
      <div class="toolbar">
        <div class="toolbar-copy">
          <h2 id="providers-title">选择转发供应商</h2>
          <p class="provider-count">{{ loading ? '正在读取 CC Switch...' : (manageMode ? `共 ${providers.length} 个供应商，已隐藏 ${hiddenCount} 个` : `已显示 ${visibleProviders.length} 个 ${uiConfig.provider_label || ''} 供应商`) }}<button class="health-refresh-button" type="button" title="刷新服务器检测数据" aria-label="刷新服务器检测数据" @click="loadHealth"><UiIcon name="refresh" /></button></p>
        </div>
        <div class="toolbar-actions">
          <label class="search"><span class="visually-hidden">搜索供应商</span><UiIcon class="search-icon" name="search" /><input v-model="query" type="search" placeholder="搜索名称或地址" autocomplete="off" /></label>
          <div class="time-window-control usage-window-wrap">
            <TimeRangeSelect v-model="usageWindow" aria-label="Token 统计时间范围" title="Token 统计时间范围" icon="clock" :options="usageWindowOptions" :initial-range="customRange" @change="loadStatus" @apply="applyCustomRange" />
          </div>
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
        <div class="usage-metric total"><span>Token 总量</span><strong>{{ formatTokenCount(usage.total_tokens) }}</strong></div>
        <div class="usage-metric"><span>输入 Token</span><strong>{{ formatTokenCount(usage.input_tokens) }}</strong></div>
        <div class="usage-metric"><span>输出 Token</span><strong>{{ formatTokenCount(usage.output_tokens) }}</strong></div>
        <div class="usage-metric"><span>缓存 Token</span><strong>{{ formatTokenCount(usage.cached_tokens) }}</strong></div>
        <div class="usage-estimate-wrap"><span v-if="usage.estimated_requests" id="usage-estimated-note">含估算请求</span><button class="usage-share-button" type="button" title="生成今日 Token 分享卡片" aria-label="生成今日 Token 分享卡片" @click="shareCardOpen = true"><UiIcon name="share" />分享卡片</button></div>
      </div>

      <div class="provider-list-shell" :class="{ managing: manageMode }">
        <div v-if="manageMode" class="manage-hint">拖动左侧手柄调整顺序；搜索时暂停拖动。隐藏的供应商仅在管理模式显示。</div>
        <div class="list-header" aria-hidden="true"><span>状态</span><span>名称和地址</span><span>服务器检测</span><span>Token 用量</span><span>请求次数</span><span>配置</span><span v-if="manageMode">操作</span></div>
        <div v-if="filteredProviders.length" class="provider-list" aria-live="polite">
          <article v-for="provider in filteredProviders" :key="provider.provider_id" class="provider-row" :class="{ current: provider.current, managing: manageMode, 'hidden-provider': provider.hidden, dragging: draggedId === provider.provider_id }" :draggable="manageMode && !query" @dragstart="startDrag(provider)" @dragend="draggedId = ''" @dragover.prevent @drop.prevent="dropProvider(provider)" @click="!manageMode && !provider.current && selectProvider(provider)">
            <div class="provider-state"><UiIcon v-if="manageMode" name="grip" class="drag-handle" /><span class="dot" /><span>{{ provider.current ? '当前' : (provider.hidden ? '隐藏' : '可用') }}</span></div>
            <div class="provider-copy">
              <div class="provider-title">
                <button class="provider-select" type="button" :disabled="provider.current || manageMode || switchingId === provider.provider_id" @click.stop="selectProvider(provider)"><strong>{{ provider.name }}</strong></button>
              </div>
              <code>{{ provider.endpoint || '未提供地址' }}</code>
            </div>
            <div class="provider-health-cell">
              <div class="provider-health-summary">
                <div class="provider-health-top"><span class="provider-health-label"><span class="provider-health-dot" :class="healthState(provider)" />{{ healthLabel(provider) }}</span><span class="provider-health-latency">{{ healthLatency(provider) }}</span></div>
                <div class="provider-health-meta"><span>24h {{ healthAvailability(provider) }}</span><span>{{ healthTime(provider) }}</span></div>
                <div class="provider-health-history" aria-label="最近 16 次检测" @pointermove="handleHistoryPointerMove($event, historyEntries(healthFor(provider)?.history, 16), '最近 16 次检测')" @pointerleave="handleHistoryPointerLeave($event)" @click.stop="handleHistoryClick($event, historyEntries(healthFor(provider)?.history, 16), '最近 16 次检测')"><span v-for="(entry, index) in historyEntries(healthFor(provider)?.history, 16)" :key="index" class="provider-health-mark" :class="healthStateTone(entry?.state)" :title="historyTitle(entry)" /></div>
              </div>
              <button v-if="healthFor(provider)" class="provider-health-toggle" type="button" title="查看检测详情" :aria-expanded="isHealthDetailsOpen(provider)" @click.stop="openHealthDetails(provider, $event.currentTarget)">详情</button>
            </div>
            <button class="provider-token-cell" type="button" :disabled="!providerUsage(provider.provider_id).request_count" @click.stop="openUsageHistory(provider, $event.currentTarget)">
              <strong :class="providerUsage(provider.provider_id).total_tokens ? 'provider-token-value' : 'provider-token-zero'">{{ formatTokenCount(providerUsage(provider.provider_id).total_tokens) }}</strong>
              <span v-if="providerUsage(provider.provider_id).request_count" class="provider-token-detail-icon">i</span>
            </button>
            <div class="provider-request-cell"><button v-if="provider.active_requests" class="active-badge" type="button" :disabled="manageMode" :title="sessionNames(provider)" @click.stop="$emit('navigate-requests', provider.provider_id)">{{ provider.active_requests }} 活动</button><span class="provider-request-count">{{ providerUsage(provider.provider_id).request_count || 0 }}</span></div>
            <div class="provider-meta">
              <button v-if="showLaunchCommand" class="copy-command" type="button" title="复制临时启动命令" @click.stop="copyLaunchCommand(provider)">临时启动</button>
              <button v-if="showStatusUpload && provider.status_upload_supported" class="copy-command status-upload-button" type="button" @click.stop="openStatusUpload(provider)">上传检测</button>
            </div>
            <div v-if="manageMode" class="provider-management-cell">
              <div class="provider-row-actions">
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
        <div class="recovery-heading"><strong>{{ recoveryTitle }}</strong><button v-if="hasRecoveryDetails" ref="recoveryDetailsButton" class="recovery-details-button" type="button" aria-label="查看最近 24 小时恢复记录" :aria-expanded="recoveryPopoverOpen" @click.stop="toggleRecoveryDetails">i</button></div>
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
        <label class="provider-editor-field"><span>模型重写 <small>留空则透传客户端模型名</small></span><input v-model.trim="form.model" type="text" maxlength="240" spellcheck="false" autocomplete="off" placeholder="例如 deepseek-v4-pro" /></label>
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

    <div v-if="selectedHealth" ref="healthPopover" class="provider-health-popover show" :style="healthPopoverStyle" role="dialog" aria-label="服务器检测详情"><div class="provider-health-detail"><div class="provider-health-detail-head"><strong>服务器检测详情</strong><span class="provider-health-detail-meta"><span>最后探测 {{ healthLastChecked(selectedHealth) }}</span><button class="provider-health-close" type="button" aria-label="关闭检测详情" @click="closeHealthDetails"><UiIcon name="close" /></button></span></div><div class="provider-health-metrics"><span><span>24 小时可用率</span><strong>{{ healthAvailabilityValue(selectedHealth.availability) }}</strong></span><span><span>最近延迟</span><strong>{{ formatLatency(selectedHealth.latest_latency) }}</strong></span><span><span>检测模型</span><strong>{{ healthModels(selectedHealth).length }} 个</strong></span></div><div v-for="model in healthModels(selectedHealth)" :key="model.model" class="provider-health-model"><span class="provider-health-model-copy"><span class="provider-health-model-title"><span class="provider-health-dot" :class="healthStateTone(model.state)" /> <strong>{{ model.model }}</strong></span><span>{{ model.source === 'manual' ? `手动检测 · ${healthLabelValue(model.state)} · ${formatLatency(model.latestLatency)}` : `${healthLabelValue(model.state)} · ${healthAvailabilityValue(model.availability)}` }}</span></span><span class="provider-health-model-history"><span class="provider-health-history-label"><span>{{ model.source === 'manual' ? '最近点击检测' : '最近 60 次' }}</span><span>较早 → 最近</span></span><span class="provider-health-history full" aria-label="模型最近 60 次检测" @pointermove="handleHistoryPointerMove($event, historyEntries(model.history, 60), `${model.model} 最近检测历史`)" @pointerleave="handleHistoryPointerLeave($event)" @click.stop="handleHistoryClick($event, historyEntries(model.history, 60), `${model.model} 最近检测历史`)"><span v-for="(entry, index) in historyEntries(model.history, 60)" :key="index" class="provider-health-mark" :class="healthStateTone(entry?.state)" :title="historyTitle(entry)" /></span></span></div></div></div>
    <div v-if="historyDetail" ref="historyDetailPopover" class="history-detail-popover show" :style="historyDetailStyle" role="status" @pointerleave="hideHistoryDetail"><strong>{{ historyDetailLabel }}</strong><span>{{ historyMeta(historyDetail) }}</span><span v-if="historyReason(historyDetail)">原因：{{ historyReason(historyDetail) }}</span></div>
    <div v-if="selectedUsage" ref="usageHistoryPopover" class="usage-history-popover show" :style="usagePopoverStyle" role="dialog" aria-label="请求记录"><div class="usage-history-heading"><div><strong>{{ selectedUsage.name }} 请求记录</strong><span>{{ usageWindowLabel }} · {{ usageHistoryTotalCount }} 条</span></div><button class="usage-history-close" type="button" aria-label="关闭用量记录" @click="closeUsageHistory"><UiIcon name="close" /></button></div><div class="usage-history-summary"><span>Token {{ formatTokenCount(usageHistoryTotals?.total_tokens ?? providerUsage(selectedUsage.provider_id).total_tokens) }}</span><span>成功 {{ formatTokenCount(usageHistoryTotals?.successful_tokens) }}</span><span>失败 {{ formatTokenCount(usageHistoryTotals?.failed_tokens) }}</span></div><ol class="usage-history-list"><li v-if="usageHistoryLoading" class="usage-history-empty">正在读取请求记录…</li><li v-else-if="usageHistoryError" class="usage-history-empty">{{ usageHistoryError }}</li><li v-else-if="!usageHistoryItems.length" class="usage-history-empty">当前时间范围内没有请求记录</li><li v-for="item in usageHistoryItems" v-else :key="item.id || item.recorded_at" class="usage-history-item" :class="{ failed: item.succeeded !== true }"><div class="usage-history-item-top"><time class="usage-history-time">{{ formatHistoryTime(item.recorded_at) }}</time><strong class="usage-history-model" :title="item.model || 'unknown'">{{ item.model || 'unknown' }}</strong><strong class="usage-history-total">{{ formatTokenCount(item.total_tokens) }}</strong></div><div class="usage-history-detail"><span>输入 {{ formatTokenCount(item.input_tokens) }}</span><span>输出 {{ formatTokenCount(item.output_tokens) }}</span><span v-if="item.cached_tokens">缓存 {{ formatTokenCount(item.cached_tokens) }}</span><span v-if="item.reasoning_tokens">推理 {{ formatTokenCount(item.reasoning_tokens) }}</span><span>{{ item.succeeded === true ? '成功' : '失败' }}</span></div></li></ol><button v-if="usageHistoryNextCursor" class="usage-history-more" type="button" :disabled="usageHistoryLoading" @click="loadUsageHistory">{{ usageHistoryLoading ? '加载中…' : '加载更早记录' }}</button></div>
    <div v-if="recoveryPopoverOpen" ref="recoveryPopover" class="recovery-popover show" :style="recoveryPopoverStyle" role="dialog" aria-label="恢复记录"><div class="recovery-popover-heading"><strong>恢复记录</strong><span>近 24 小时 · {{ recoveryTotalCount }} 条</span><button class="usage-history-close" type="button" aria-label="关闭恢复记录" @click="closeRecoveryDetails"><UiIcon name="close" /></button></div><ol><li v-if="recoveryLoading" class="recovery-empty">正在加载恢复记录…</li><li v-else-if="recoveryError" class="recovery-empty">{{ recoveryError }}</li><li v-else-if="!recoveryItems.length" class="recovery-empty">近 24 小时没有恢复记录</li><li v-for="item in recoveryItems" v-else :key="recoveryItemKey(item)"><span class="recovery-error-meta">{{ recoveryMeta(item) }}</span><span class="recovery-error-summary">{{ item.summary || '上游临时错误' }}</span></li></ol><button v-if="recoveryNextCursor" class="usage-history-more" type="button" :disabled="recoveryLoading" @click="loadRecoveryHistory({ loadMore: true })">{{ recoveryLoading ? '加载中…' : '加载更早记录' }}</button></div>

    <ShareCardDialog v-if="shareCardOpen" @close="shareCardOpen = false" />
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
import TimeRangeSelect from './TimeRangeSelect.vue'
import ShareCardDialog from './ShareCardDialog.vue'
import UiSelect from './ui/UiSelect.vue'
import UiIcon from './ui/UiIcon.vue'
import { formatTokenCount } from '../token-format.js'

const status = ref({ providers: [], usage: { total: {}, by_provider: {} }, retry: {} })
const uiConfig = ref({ features: {} })
const props = defineProps({ showLaunchCommand: { type: Boolean, default: true }, showStatusUpload: { type: Boolean, default: true } })
const emit = defineEmits(['navigate-requests', 'notify'])
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
  { value: 'all', label: '全部' }
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
const healthAnchor = ref(null)
const healthPopover = ref(null)
const healthPopoverStyle = ref({})
const historyDetail = ref(null)
const historyDetailLabel = ref('')
const historyDetailAnchor = ref(null)
const historyDetailPopover = ref(null)
const historyDetailStyle = ref({})
const historyDetailPinned = ref(false)
const selectedUsage = ref(null)
const usageHistoryPopover = ref(null)
const usageHistoryItems = ref([])
const usageHistoryTotals = ref(null)
const usageHistoryNextCursor = ref('')
const usageHistoryTotalCount = ref(0)
const usageHistoryLoading = ref(false)
const usageHistoryError = ref('')
const usageHistoryAnchor = ref(null)
const usagePopoverStyle = ref({})
const recoveryPopoverOpen = ref(false)
const recoveryPopover = ref(null)
const recoveryDetailsButton = ref(null)
const recoveryPopoverStyle = ref({})
const recoveryItems = ref([])
const recoveryNextCursor = ref('')
const recoveryTotalCount = ref(0)
const recoveryLoading = ref(false)
const recoveryError = ref('')
const customRange = ref(null)
let timer

const shareCardOpen = ref(false)
const emptyForm = () => ({ name: '', base_url: '', api_key: '', clear_api_key: false, transport: 'httpx', wire_api: 'responses', model: '', headers: '{}', query_params: '{}' })
const form = ref(emptyForm())
const providers = computed(() => status.value.providers || [])
const catalogEnabled = computed(() => uiConfig.value.features?.provider_catalog === true)
const showLaunchCommand = computed(() => uiConfig.value.features?.provider_launch_command !== false && props.showLaunchCommand)
const showStatusUpload = computed(() => uiConfig.value.features?.status_upload !== false && props.showStatusUpload)
const visibleProviders = computed(() => providers.value.filter(provider => manageMode.value || !provider.hidden))
const hiddenCount = computed(() => providers.value.filter(provider => provider.hidden).length)
const filteredProviders = computed(() => { const term = query.value.trim().toLocaleLowerCase(); return visibleProviders.value.filter(provider => !term || [provider.name, provider.endpoint].some(value => String(value || '').toLocaleLowerCase().includes(term))) })
const currentProvider = computed(() => providers.value.find(provider => provider.current || provider.provider_id === status.value.current_provider_id))
const usage = computed(() => status.value.usage?.total || {})
const drainingRequests = computed(() => providers.value.reduce((sum, provider) => sum + Number(provider.draining_requests || 0), 0))
const lastRequestLabel = computed(() => status.value.last_status_code ? `HTTP ${status.value.last_status_code}` : '尚无请求')
const recoveryTitle = computed(() => status.value.retry?.circuit_open?.length ? '自动恢复已暂停' : (status.value.retry?.active?.length ? '正在自动恢复' : '自动恢复已就绪'))
const hasRecoveryDetails = computed(() => Boolean(status.value.retry?.recent_errors?.length || status.value.retry?.history?.total_count))
const wireApiLabel = computed(() => currentProvider.value?.wire_api === 'responses' ? 'Responses · SSE' : currentProvider.value?.wire_api === 'anthropic_messages' ? 'Messages · SSE' : (currentProvider.value?.wire_api || uiConfig.value.protocol_label || '—'))
const usageWindowLabel = computed(() => ({ today: '今日', '24h': '近 24 小时', '7d': '近 7 天', '30d': '近 30 天', all: '全部' })[usageWindow.value])

function providerUsage(id) { return status.value.usage?.by_provider?.[id] || {} }
function formatHistoryTime(value) { const number = Number(value); const date = Number.isFinite(number) && number > 0 ? new Date(number < 10_000_000_000 ? number * 1000 : number) : new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function positionUsageHistory() {
  const anchor = usageHistoryAnchor.value
  const popover = usageHistoryPopover.value
  if (!anchor || !popover) return
  const margin = 12
  const rect = anchor.getBoundingClientRect()
  const width = Math.min(480, window.innerWidth - margin * 2)
  const height = Math.min(popover.getBoundingClientRect().height, window.innerHeight - margin * 2)
  const left = Math.min(window.innerWidth - width - margin, Math.max(margin, rect.right - width))
  const top = rect.bottom + 8 + height <= window.innerHeight - margin ? rect.bottom + 8 : Math.max(margin, rect.top - height - 8)
  usagePopoverStyle.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` }
}
function usageHistoryParams(cursor = '') {
  const params = new URLSearchParams({ provider_id: selectedUsage.value.provider_id })
  if (usageWindow.value === 'custom' && customRange.value) { params.set('start_at', String(customRange.value.startAt)); params.set('end_at', String(customRange.value.endAt)) } else params.set('usage_window', usageWindow.value)
  if (cursor) params.set('cursor', cursor)
  return params
}
async function loadUsageHistory({ reset = false } = {}) {
  if (!selectedUsage.value || usageHistoryLoading.value) return
  usageHistoryLoading.value = true; usageHistoryError.value = ''
  if (reset) { usageHistoryItems.value = []; usageHistoryNextCursor.value = ''; usageHistoryTotals.value = null; usageHistoryTotalCount.value = 0 }
  try {
    const result = await controlFetch(`/api/usage-history?${usageHistoryParams(reset ? '' : usageHistoryNextCursor.value)}`)
    usageHistoryItems.value = reset ? (result.items || []) : [...usageHistoryItems.value, ...(result.items || [])]
    usageHistoryNextCursor.value = result.next_cursor || ''
    usageHistoryTotalCount.value = Number(result.total_count || 0)
    usageHistoryTotals.value = result.total || null
  } catch (error) { usageHistoryError.value = error.message || '无法读取请求记录' } finally { usageHistoryLoading.value = false; await nextTick(); positionUsageHistory() }
}
async function openUsageHistory(provider, anchor) {
  if (selectedUsage.value?.provider_id === provider.provider_id) { closeUsageHistory(); return }
  selectedHealth.value = null; selectedUsage.value = provider; usageHistoryAnchor.value = anchor
  await nextTick(); positionUsageHistory(); await loadUsageHistory({ reset: true })
}
function closeUsageHistory() { selectedUsage.value = null; usageHistoryAnchor.value = null; usageHistoryItems.value = []; usageHistoryNextCursor.value = ''; usageHistoryTotals.value = null; usageHistoryError.value = '' }
function recoveryItemKey(item) { return [item.recorded_at, item.request_id, item.provider_id, item.attempt, item.outcome].join('-') }
function recoveryMeta(item) { const provider = providers.value.find(value => value.provider_id === item.provider_id); const kind = ({ rate_limited: '频率限制', model_capacity: '模型容量', connection: '连接失败', stream_start: '流启动失败', stream_idle_timeout: '流空闲超时' })[item.kind] || item.kind || '临时故障'; const outcome = ({ retrying: '已安排重试', exhausted: '重试已结束', client_disconnected: '客户端已断开', passed_through: '已透传' })[item.outcome] || '已记录'; return [`失败 ${formatHistoryTime(item.recorded_at)}`, provider?.name || item.provider_id || '未知供应商', `第 ${item.attempt || 1} 次请求`, kind, outcome].join(' · ') }
function positionRecoveryDetails() { const anchor = recoveryDetailsButton.value; const popover = recoveryPopover.value; if (!anchor || !popover) return; const margin = 12; const gap = 8; const anchorRect = anchor.getBoundingClientRect(); const popoverRect = popover.getBoundingClientRect(); const left = Math.min(window.innerWidth - popoverRect.width - margin, Math.max(margin, anchorRect.right - popoverRect.width)); const below = anchorRect.bottom + gap; const above = anchorRect.top - popoverRect.height - gap; const top = below + popoverRect.height <= window.innerHeight - margin ? below : Math.max(margin, above); recoveryPopoverStyle.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` } }
async function loadRecoveryHistory({ loadMore = false } = {}) { if (recoveryLoading.value || (!loadMore && !hasRecoveryDetails.value)) return; recoveryLoading.value = true; recoveryError.value = ''; try { const params = new URLSearchParams({ limit: '50' }); if (loadMore && recoveryNextCursor.value) params.set('cursor', recoveryNextCursor.value); const result = await controlFetch(`/api/recovery-history?${params}`); recoveryItems.value = loadMore ? [...recoveryItems.value, ...(result.items || [])] : (result.items || []); recoveryNextCursor.value = result.next_cursor || ''; recoveryTotalCount.value = Number(result.total_count || recoveryItems.value.length) } catch (error) { recoveryError.value = error.message || '无法读取恢复记录' } finally { recoveryLoading.value = false; await nextTick(); positionRecoveryDetails() } }
async function toggleRecoveryDetails() { if (recoveryPopoverOpen.value) { closeRecoveryDetails(); return } recoveryPopoverOpen.value = true; recoveryItems.value = []; recoveryNextCursor.value = ''; recoveryTotalCount.value = 0; await nextTick(); positionRecoveryDetails(); await loadRecoveryHistory() }
function closeRecoveryDetails() { recoveryPopoverOpen.value = false; recoveryItems.value = []; recoveryNextCursor.value = ''; recoveryTotalCount.value = 0; recoveryError.value = '' }
function sessionNames(provider) { return (provider.active_sessions || []).map(item => item.name || item.session_name || item).join('\n') }
function normalizeEndpoint(value) { const raw = String(value || '').trim(); if (!raw) return ''; try { const parsed = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`); const path = parsed.pathname.replace(/\/{2,}/g, '/').replace(/\/+$/, ''); return parsed.host.toLocaleLowerCase() + (path === '/' ? '' : path) } catch { return raw.toLocaleLowerCase().replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').replace(/[?#].*$/, '').replace(/\/+$/, '') } }
function endpointsMatch(left, right) { const a = normalizeEndpoint(left); const b = normalizeEndpoint(right); if (!a || !b) return false; if (a === b) return true; const shorter = a.length < b.length ? a : b; const longer = shorter === a ? b : a; return longer.startsWith(shorter) && longer.charAt(shorter.length) === '/' }
function healthFor(provider) { const candidates = Array.isArray(health.value?.providers) ? health.value.providers : []; const endpoint = normalizeEndpoint(provider.endpoint); const exact = candidates.find(item => normalizeEndpoint(item?.base_url) === endpoint); if (exact) return exact; const prefixMatches = candidates.filter(item => endpointsMatch(item?.base_url, endpoint)); return prefixMatches.length === 1 ? prefixMatches[0] : null }
function healthState(provider) { return healthStateTone(healthFor(provider)?.state) }
function healthLabel(provider) { return healthLabelValue(healthFor(provider)?.state) }
function healthLatency(provider) { const health = healthFor(provider); return formatLatency(health?.latest_latency ?? health?.latency_ms ?? health?.latency) }
function formatLatency(value) { const milliseconds = Number(value); if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—'; return milliseconds < 1000 ? `${Math.round(milliseconds)} ms` : `${Number((milliseconds / 1000).toFixed(1))} 秒` }
function healthAvailability(provider) { const value = healthFor(provider)?.availability ?? healthFor(provider)?.uptime; return Number.isFinite(Number(value)) ? `${Number(Number(value).toFixed(2))}%` : '—' }
function healthTime(provider) { const value = healthFor(provider)?.last_checked || healthFor(provider)?.checked_at || healthFor(provider)?.updated_at; return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }
function normalizeHealthState(value) { const state = String(value || 'unknown').toLowerCase(); if (state === 'up') return 'healthy'; return ['healthy', 'degraded', 'recovering', 'down'].includes(state) ? state : 'unknown' }
function healthStateTone(value) { return ({ healthy: 'healthy', degraded: 'warning', recovering: 'warning', down: 'down', unknown: 'unknown' })[normalizeHealthState(value)] }
function historyEntries(history, limit) { const items = Array.isArray(history) ? history.slice(0, limit).reverse() : []; while (items.length < limit) items.unshift(null); return items }
function historyMeta(entry) { if (!entry) return '暂无检测记录'; const latency = entry.latency_ms ?? entry.latency; return [formatHistoryTime(entry.recorded_at || entry.finished_at), healthLabelValue(entry.state), latency == null ? '' : formatLatency(latency)].filter(Boolean).join(' · ') }
function historyReason(entry) { return entry?.error_summary || entry?.error_code || '' }
function historyTitle(entry) { if (!entry) return '暂无检测记录'; return [historyMeta(entry), historyReason(entry) ? `原因：${historyReason(entry)}` : ''].filter(Boolean).join(' · ') }
function healthLabelValue(value) { return ({ healthy: '可用', degraded: '有波动', recovering: '恢复中', down: '暂不可用', unknown: '等待检测' })[normalizeHealthState(value)] }
function healthAvailabilityValue(value) { return Number.isFinite(Number(value)) ? `${Number(Number(value).toFixed(2))}%` : '—' }
function healthLastChecked(providerHealth) { return formatHistoryTime(providerHealth?.last_checked || providerHealth?.checked_at || providerHealth?.updated_at) }
function healthModels(providerHealth) { const automatic = Array.isArray(providerHealth?.models) ? providerHealth.models : []; const byName = new Map(automatic.map(model => [String(model?.model || ''), model])); const manual = providerHealth?.manual_history && typeof providerHealth.manual_history === 'object' ? providerHealth.manual_history : {}; const names = [...new Set([...(providerHealth?.display_models || []), ...automatic.map(model => model?.model), ...Object.keys(manual)].filter(Boolean))]; return names.map(name => { const model = byName.get(name); if (model) return { model: name, state: normalizeHealthState(model.state), availability: model.availability, latestLatency: model.latest_latency, history: model.history, source: 'automatic' }; const items = Array.isArray(manual[name]) ? manual[name] : []; return { model: name, state: items[0] ? (items[0].success ? 'healthy' : 'down') : 'unknown', availability: null, latestLatency: items[0]?.latency_ms, history: items.map(item => ({ ...item, state: item?.success ? 'healthy' : 'down' })), source: 'manual' } }) }
function historyIndex(event, entries) { const rect = event.currentTarget.getBoundingClientRect(); const count = entries.length; if (!count || !rect.width) return -1; return Math.max(0, Math.min(count - 1, Math.floor(((event.clientX - rect.left) / rect.width) * count))) }
function showHistoryDetail(entry, label, anchor) { if (!entry || !anchor) { if (!historyDetailPinned.value) hideHistoryDetail(); return } historyDetail.value = entry; historyDetailLabel.value = label; historyDetailAnchor.value = anchor; nextTick(positionHistoryDetail) }
function handleHistoryPointerMove(event, entries, label) { if (event.pointerType && event.pointerType !== 'mouse' || historyDetailPinned.value) return; const index = historyIndex(event, entries); if (index >= 0) showHistoryDetail(entries[index], label, event.currentTarget.children[index]) }
function handleHistoryPointerLeave(event) { if (event.pointerType === 'mouse' && !historyDetailPinned.value) hideHistoryDetail() }
function handleHistoryClick(event, entries, label) { const index = historyIndex(event, entries); if (index < 0 || !entries[index]) return; historyDetailPinned.value = true; showHistoryDetail(entries[index], label, event.currentTarget.children[index]) }
function hideHistoryDetail({ force = false } = {}) { if (historyDetailPinned.value && !force) return; historyDetailPinned.value = false; historyDetail.value = null; historyDetailLabel.value = ''; historyDetailAnchor.value = null; historyDetailStyle.value = {} }
function positionHistoryDetail() { const anchor = historyDetailAnchor.value; const popover = historyDetailPopover.value; if (!anchor || !popover) return; const rect = anchor.getBoundingClientRect(); const popoverRect = popover.getBoundingClientRect(); const margin = 10; const left = Math.min(window.innerWidth - popoverRect.width - margin, Math.max(margin, rect.left + rect.width / 2 - popoverRect.width / 2)); const above = rect.top - popoverRect.height - 8; const top = above >= margin ? above : rect.bottom + 8; historyDetailStyle.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` } }
function isHealthDetailsOpen(provider) { return Boolean(selectedHealth.value && healthFor(provider) === selectedHealth.value) }
function openHealthDetails(provider, anchor) { if (healthAnchor.value === anchor && selectedHealth.value) { closeHealthDetails(); return } selectedUsage.value = null; closeRecoveryDetails(); hideHistoryDetail({ force: true }); selectedHealth.value = healthFor(provider); healthAnchor.value = anchor; nextTick(positionHealthDetails) }
function closeHealthDetails() { selectedHealth.value = null; healthAnchor.value = null; healthPopoverStyle.value = {}; hideHistoryDetail({ force: true }) }
function positionHealthDetails() { const anchor = healthAnchor.value; const popover = healthPopover.value; if (!anchor || !popover) return; const margin = 12; const gap = 8; const anchorRect = anchor.getBoundingClientRect(); const popoverRect = popover.getBoundingClientRect(); const left = Math.min(window.innerWidth - popoverRect.width - margin, Math.max(margin, anchorRect.right - popoverRect.width)); const below = anchorRect.bottom + gap; const above = anchorRect.top - popoverRect.height - gap; const top = below + popoverRect.height <= window.innerHeight - margin ? below : Math.max(margin, above); healthPopoverStyle.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` } }

async function loadStatus() {
  if (loading.value && providers.value.length) return
  try {
    localStorage.setItem('local-proxy-usage-window', usageWindow.value)
    if (usageWindow.value === 'custom' && !customRange.value) return
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
async function openEdit(provider) { const detail = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}`); editingId.value = provider.provider_id; form.value = { name: detail.name || '', base_url: detail.base_url || '', api_key: '', clear_api_key: false, transport: detail.transport || 'httpx', wire_api: detail.wire_api || 'responses', model: detail.model || '', headers: JSON.stringify(detail.headers || {}, null, 2), query_params: JSON.stringify(detail.query_params || {}, null, 2) }; editorOpen.value = true }
function closeEditor() { if (!saving.value) editorOpen.value = false }
function parseObject(value, label) { const parsed = JSON.parse(value || '{}'); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label} 必须是 JSON 对象`); return parsed }
async function saveProvider() { saving.value = true; formError.value = ''; try { const payload = { name: form.value.name, base_url: form.value.base_url, api_key: form.value.api_key || null, clear_api_key: form.value.clear_api_key, transport: form.value.transport, wire_api: form.value.wire_api, model: form.value.model, headers: parseObject(form.value.headers, 'Headers'), query_params: parseObject(form.value.query_params, 'Query Params') }; await controlFetch(editingId.value ? `/api/providers/${encodeURIComponent(editingId.value)}` : '/api/providers', jsonOptions(editingId.value ? 'PUT' : 'POST', payload)); editorOpen.value = false; await loadStatus() } catch (error) { formError.value = error.message } finally { saving.value = false } }
async function deleteProvider() { await controlFetch(`/api/providers/${encodeURIComponent(editingId.value)}`, { method: 'DELETE' }); editorOpen.value = false; await loadStatus() }
async function copyLaunchCommand(provider) {
  try {
    const payload = await controlFetch(`/api/providers/${encodeURIComponent(provider.provider_id)}/launch-command`, { method: 'POST' })
    await navigator.clipboard.writeText(payload.command || payload.content || String(payload))
    emit('notify', { title: '复制成功', detail: `${provider.name} 的临时启动命令已复制到剪贴板。` })
  } catch (error) {
    emit('notify', { title: '复制失败', detail: error.message, tone: 'error' })
  }
}
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
function applyCustomRange(range) { customRange.value = range; usageWindow.value = 'custom'; loadStatus() }

onMounted(() => { loadStatus(); timer = window.setInterval(() => loadStatus(), 5000); window.addEventListener('resize', positionUsageHistory); window.addEventListener('resize', positionRecoveryDetails); window.addEventListener('resize', positionHealthDetails); window.addEventListener('resize', positionHistoryDetail) })
onBeforeUnmount(() => { window.clearInterval(timer); window.removeEventListener('resize', positionUsageHistory); window.removeEventListener('resize', positionRecoveryDetails); window.removeEventListener('resize', positionHealthDetails); window.removeEventListener('resize', positionHistoryDetail) })
</script>
