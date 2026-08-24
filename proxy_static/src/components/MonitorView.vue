<template>
  <section id="monitor-view" class="settings-view view-panel" aria-labelledby="monitor-title">
    <div class="settings-heading"><div><h2 id="monitor-title">监控管理</h2><p>{{ loading ? '正在读取服务器上的全部监控供应商' : `服务器上共有 ${providers.length} 个监控供应商` }}</p></div><div class="monitor-actions"><button class="icon-button" type="button" title="刷新监控列表" aria-label="刷新监控列表" @click="loadProviders"><UiIcon name="refresh" /></button></div></div>
    <p v-if="error" class="status-upload-error" role="alert">{{ error }}</p>
    <div v-if="providers.length" class="monitor-list" aria-live="polite">
      <div v-for="(provider, index) in providers" :key="provider.id" class="monitor-row">
        <div class="monitor-main"><strong class="monitor-name">{{ provider.name || provider.id }}</strong><span class="monitor-endpoint">{{ provider.endpoint || provider.base_url || '—' }}</span><span class="monitor-models">{{ modelLabel(provider) }}</span></div>
        <strong class="monitor-state" :class="provider.state || 'unknown'">{{ stateLabel(provider.state) }}</strong>
        <div class="monitor-controls"><button type="button" :disabled="index === 0" @click="move(index, -1)">上移</button><button type="button" :disabled="index === providers.length - 1" @click="move(index, 1)">下移</button><button type="button" :disabled="probingId === provider.id" @click="probe(provider)">{{ probingId === provider.id ? '提交中' : '立即检测' }}</button><button type="button" @click="remove(provider)">删除</button></div>
      </div>
    </div>
    <div v-else-if="!loading" class="empty-state">服务器上没有监控供应商</div>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
import UiIcon from './ui/UiIcon.vue'
const providers = ref([])
const loading = ref(true)
const error = ref('')
const probingId = ref('')
function modelLabel(provider) { const models = provider.display_models || provider.models || []; return models.length ? models.join(' · ') : '未设置检测模型' }
function stateLabel(value) { return ({ healthy: '正常', degraded: '波动', down: '不可用', probing: '检测中', unknown: '等待检测' })[value] || '等待检测' }
async function request(payload) { return controlFetch('/api/status-management', jsonOptions('POST', payload)) }
async function loadProviders() { loading.value = true; error.value = ''; try { const result = await request({ action: 'list' }); providers.value = result.providers || [] } catch (requestError) { error.value = requestError.message } finally { loading.value = false } }
async function move(index, offset) { const next = providers.value.slice(); [next[index], next[index + offset]] = [next[index + offset], next[index]]; await request({ action: 'order', provider_ids: next.map(item => item.id) }); providers.value = next }
async function probe(provider) { probingId.value = provider.id; try { await controlFetch(`/api/status-management/providers/${encodeURIComponent(provider.id)}/probe`, jsonOptions('POST', { models: provider.display_models || provider.models || [] })) } catch (requestError) { error.value = requestError.message } finally { probingId.value = '' } }
async function remove(provider) { if (!window.confirm(`确定删除服务器监控供应商“${provider.name || provider.id}”吗？`)) return; try { await request({ action: 'delete', provider_id: provider.id }); await loadProviders() } catch (requestError) { error.value = requestError.message } }
onMounted(loadProviders)
</script>
