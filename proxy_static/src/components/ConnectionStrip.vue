<template>
  <div class="connection-strip">
    <div class="connection-value"><span>固定连接</span><code>{{ config.proxy_url || '—' }}</code></div>
    <div class="status-group"><span class="dot" :class="{ offline: error }" /><span>{{ error || '本机可用' }}</span></div>
    <div class="connection-value database"><span>数据来源</span><code>{{ databasePath }}</code></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { controlFetch } from '../api.js'
defineProps({ config: { type: Object, required: true } })
const error = ref('')
const databasePath = ref('~/.cc-switch/cc-switch.db')
let timer
async function checkConnection() {
  try { await controlFetch('/api/status'); error.value = '' } catch { error.value = '连接已中断' }
}
async function loadRuntime() {
  try { const runtime = await controlFetch('/api/runtime-settings'); databasePath.value = runtime.database_path || runtime.provider_catalog_source || databasePath.value } catch {}
}
onMounted(() => { checkConnection(); loadRuntime(); timer = window.setInterval(checkConnection, 5000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>
