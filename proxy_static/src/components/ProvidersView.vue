<template>
  <section id="providers-view" class="providers-view view-panel" aria-labelledby="providers-title">
    <div class="view-heading">
      <h2 id="providers-title">供应商管理</h2>
      <button class="primary-button" @click="showAddForm = true">+ 添加供应商</button>
    </div>

    <div v-if="providers.length === 0" class="empty-state">
      <p>暂无供应商配置</p>
    </div>

    <div v-else class="providers-list">
      <div v-for="provider in providers" :key="provider.name" class="provider-card">
        <div class="provider-card-header">
          <h3 class="provider-name">{{ provider.name }}</h3>
          <div class="provider-actions">
            <button class="icon-button" @click="editProvider(provider)" aria-label="编辑">✏️</button>
            <button class="icon-button" @click="deleteProvider(provider.name)" aria-label="删除">🗑️</button>
          </div>
        </div>
        <div class="provider-card-body">
          <div class="provider-detail">
            <span class="detail-label">Base URL</span>
            <code class="detail-value">{{ provider.base_url }}</code>
          </div>
          <div class="provider-detail" v-if="provider.api_key">
            <span class="detail-label">API Key</span>
            <code class="detail-value">{{ maskApiKey(provider.api_key) }}</code>
          </div>
          <div class="provider-detail" v-if="provider.enabled !== undefined">
            <span class="detail-label">状态</span>
            <span class="detail-value" :class="provider.enabled ? 'status-enabled' : 'status-disabled'">
              {{ provider.enabled ? '已启用' : '已禁用' }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Add/Edit Form Modal -->
    <div v-if="showAddForm || editingProvider" class="modal-overlay" @click.self="closeForm">
      <div class="modal-content">
        <div class="modal-header">
          <h3>{{ editingProvider ? '编辑供应商' : '添加供应商' }}</h3>
          <button class="icon-button" @click="closeForm">✕</button>
        </div>
        <form @submit.prevent="saveProvider" class="provider-form">
          <label class="form-group">
            <span>名称</span>
            <input v-model="formData.name" type="text" required :disabled="!!editingProvider" />
          </label>
          <label class="form-group">
            <span>Base URL</span>
            <input v-model="formData.base_url" type="url" required />
          </label>
          <label class="form-group">
            <span>API Key</span>
            <input v-model="formData.api_key" type="text" />
          </label>
          <label class="form-group">
            <span>Headers (JSON)</span>
            <textarea v-model="formData.headers" rows="3" placeholder='{"Authorization": "Bearer ..."}'></textarea>
          </label>
          <label class="form-group">
            <span>Query Params (JSON)</span>
            <textarea v-model="formData.query_params" rows="3" placeholder='{"version": "v1"}'></textarea>
          </label>
          <label class="form-group checkbox-group">
            <input v-model="formData.enabled" type="checkbox" />
            <span>启用此供应商</span>
          </label>
          <div class="form-actions">
            <button type="button" class="secondary-button" @click="closeForm">取消</button>
            <button type="submit" class="primary-button">保存</button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const providers = ref([])
const showAddForm = ref(false)
const editingProvider = ref(null)
const formData = ref({
  name: '',
  base_url: '',
  api_key: '',
  headers: '',
  query_params: '',
  enabled: true
})

function maskApiKey(key) {
  if (!key || key.length < 8) return '***'
  return key.substring(0, 4) + '...' + key.substring(key.length - 4)
}

function editProvider(provider) {
  editingProvider.value = provider
  formData.value = {
    name: provider.name,
    base_url: provider.base_url,
    api_key: provider.api_key || '',
    headers: provider.headers ? JSON.stringify(provider.headers, null, 2) : '',
    query_params: provider.query_params ? JSON.stringify(provider.query_params, null, 2) : '',
    enabled: provider.enabled !== false
  }
}

function closeForm() {
  showAddForm.value = false
  editingProvider.value = null
  formData.value = {
    name: '',
    base_url: '',
    api_key: '',
    headers: '',
    query_params: '',
    enabled: true
  }
}

async function saveProvider() {
  const payload = {
    name: formData.value.name,
    base_url: formData.value.base_url,
    api_key: formData.value.api_key || null,
    headers: formData.value.headers ? JSON.parse(formData.value.headers) : {},
    query_params: formData.value.query_params ? JSON.parse(formData.value.query_params) : {},
    enabled: formData.value.enabled
  }

  try {
    const method = editingProvider.value ? 'PUT' : 'POST'
    const response = await fetch('/control/providers', {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })

    if (response.ok) {
      await loadProviders()
      closeForm()
    }
  } catch (error) {
    console.error('Failed to save provider:', error)
  }
}

async function deleteProvider(name) {
  if (!confirm(`确认删除供应商 "${name}"？`)) return

  try {
    const response = await fetch(`/control/providers/${name}`, { method: 'DELETE' })
    if (response.ok) {
      await loadProviders()
    }
  } catch (error) {
    console.error('Failed to delete provider:', error)
  }
}

async function loadProviders() {
  try {
    const response = await fetch('/control/providers')
    if (response.ok) {
      providers.value = await response.json()
    }
  } catch (error) {
    console.error('Failed to load providers:', error)
  }
}

onMounted(() => {
  loadProviders()
})
</script>
