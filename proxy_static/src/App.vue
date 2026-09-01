<template>
  <main class="stage">
    <section class="app-window" aria-label="本地中转控制台">
      <Titlebar :config="config" />
      <ViewTabs :active-view="activeView" :request-count="requestCount" :requests-enabled="config.features?.usage_history !== false" @change-view="changeView" />

      <ProvidersView v-show="activeView === 'providers'" :show-launch-command="showProviderLaunchCommand" :show-status-upload="showStatusUpload" @navigate-requests="navigateToProviderRequests" />
      <RequestsView v-if="config.features?.usage_history !== false" v-show="activeView === 'requests'" :config="config" :navigation-provider-id="requestProviderId" @count="requestCount = $event" />
      <SettingsView v-show="activeView === 'settings'" />
      <RuntimeView v-show="activeView === 'runtime'" :config="config" @launch-command-visibility-change="showProviderLaunchCommand = $event" @status-upload-visibility-change="showStatusUpload = $event" />
      <MonitorView v-show="activeView === 'monitor'" />

      <footer class="footer">
        <ConnectionStrip :config="config" />
        <div class="footer-actions">
          <button class="primary-button ccs-import-button" type="button" @click="importToCcSwitch"><UiIcon name="upload" />{{ config.config_button_label || '导入到 CCS' }}</button>
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
import { buildCcSwitchImportDeeplink } from './ccswitch.js'
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
const requestProviderId = ref('')
const toast = ref(null)
const config = ref({ display_name: '本地中转', brand_mark: 'CX', protocol_label: '从 CC Switch 读取供应商，切换后无需重启客户端', features: {} })
const showProviderLaunchCommand = ref(true)
const showStatusUpload = ref(true)

function viewStorageKey() {
  return `local-proxy-active-view-${config.value.service_id || 'local'}`
}

function changeView(view) {
  activeView.value = view
  localStorage.setItem(viewStorageKey(), view)
}

function navigateToProviderRequests(providerId) {
  requestProviderId.value = String(providerId || '')
  activeView.value = 'requests'
  localStorage.setItem(viewStorageKey(), 'requests')
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

function importToCcSwitch() {
  try {
    const deeplink = buildCcSwitchImportDeeplink({
      serviceId: config.value.service_id,
      endpoint: config.value.proxy_url,
      homepage: window.location.origin,
      providerName: config.value.display_name || 'Codex Provider Hub'
    })
    window.open(deeplink, '_self')
    window.setTimeout(() => {
      if (document.hasFocus()) showToast('导入失败', 'CC Switch 未安装或协议处理程序未注册。', 'error')
    }, 100)
  } catch (error) {
    showToast('导入失败', error.message, 'error')
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
