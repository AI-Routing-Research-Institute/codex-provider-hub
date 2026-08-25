<template>
  <main class="stage">
    <section class="app-window" aria-label="本地中转控制台">
      <Titlebar :config="config" />
      <ViewTabs :active-view="activeView" :request-count="requestCount" :requests-enabled="config.features?.usage_history !== false" @change-view="changeView" />

      <ProvidersView v-show="activeView === 'providers'" />
      <RequestsView v-if="config.features?.usage_history !== false" v-show="activeView === 'requests'" :config="config" @count="requestCount = $event" />
      <SettingsView v-show="activeView === 'settings'" />
      <RuntimeView v-show="activeView === 'runtime'" :config="config" />
      <MonitorView v-show="activeView === 'monitor'" />

      <footer class="footer">
        <ConnectionStrip :config="config" />
        <div class="footer-actions">
          <button class="text-button" type="button" @click="copyConfig">{{ config.config_button_label || '复制客户端配置' }}</button>
          <button class="power-button" type="button" title="退出本地中转" @click="shutdownProxy">退出中转</button>
        </div>
      </footer>
      <div class="toast-region" aria-live="polite" aria-atomic="true">
        <div v-if="toast" class="toast show" :class="{ error: toast.tone === 'error' }">
          <span class="toast-icon" aria-hidden="true">{{ toast.tone === 'error' ? '!' : '✓' }}</span>
          <div><strong>{{ toast.title }}</strong><span>{{ toast.detail }}</span></div>
          <button class="toast-close" type="button" aria-label="关闭通知" @click="toast = null"><UiIcon name="close" /></button>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { controlFetch } from './api.js'
import Titlebar from './components/Titlebar.vue'
import ConnectionStrip from './components/ConnectionStrip.vue'
import ViewTabs from './components/ViewTabs.vue'
import ProvidersView from './components/ProvidersView.vue'
import RequestsView from './components/RequestsView.vue'
import SettingsView from './components/SettingsView.vue'
import RuntimeView from './components/RuntimeView.vue'
import MonitorView from './components/MonitorView.vue'
import UiIcon from './components/ui/UiIcon.vue'

const activeView = ref('providers')
const requestCount = ref(0)
const toast = ref(null)
const config = ref({ display_name: '本地中转', brand_mark: 'CX', protocol_label: '从 CC Switch 读取供应商，切换后无需重启客户端', features: {} })

function viewStorageKey() {
  return `local-proxy-active-view-${config.value.service_id || 'local'}`
}

function changeView(view) {
  activeView.value = view
  localStorage.setItem(viewStorageKey(), view)
}

function applyTheme(preference) {
  const dark = preference === 'dark' || (preference === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themePreference = preference
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

function showToast(title, detail, tone = 'success') {
  toast.value = { title, detail, tone }
  window.setTimeout(() => { toast.value = null }, 4200)
}

async function copyConfig() {
  try {
    const payload = await controlFetch(config.value.config_endpoint || '/api/codex-config')
    const content = typeof payload === 'string' ? payload : (payload.config || payload.content || '')
    await navigator.clipboard.writeText(content)
    const detail = config.value.copy_config_success_detail || '客户端配置已复制'
    showToast(config.value.copy_config_success_title || '配置已复制', detail)
  } catch (error) {
    showToast('复制失败', error.message, 'error')
  }
}

async function shutdownProxy() {
  try {
    await controlFetch('/api/shutdown', { method: 'POST' })
  } catch (error) {
    showToast('退出失败', error.message, 'error')
  }
}

onMounted(async () => {
  applyTheme(localStorage.getItem('local-proxy-theme') || 'system')
  try {
    config.value = { ...config.value, ...(await controlFetch('/api/ui-config')) }
    const storedView = localStorage.getItem(viewStorageKey()) || 'providers'
    activeView.value = storedView === 'requests' && config.value.features?.usage_history === false ? 'providers' : storedView
    document.title = config.value.display_name || '本地中转'
  } catch (error) {
    showToast('界面配置读取失败', error.message, 'error')
  }
})
</script>
