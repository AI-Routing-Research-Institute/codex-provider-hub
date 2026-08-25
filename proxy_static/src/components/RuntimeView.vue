<template>
  <section id="runtime-view" class="settings-view view-panel" aria-labelledby="runtime-title">
    <div class="settings-heading">
      <div><h2 id="runtime-title">运行设置</h2><p>数据源和检测地址即时生效；端口在重新启动本地中转后生效</p></div>
      <span class="live-pill" :class="{ pending: settings.restart_required }"><span class="dot" />{{ settings.restart_required ? '等待重启' : '配置已生效' }}</span>
    </div>
    <form class="settings-form" @submit.prevent="saveSettings">
      <label class="setting-row"><span><strong>本地端口</strong><small>只监听 127.0.0.1；修改后需要重启并重新复制客户端配置</small></span><input v-model.number="settings.configured_port" type="number" min="1024" max="65535" step="1" inputmode="numeric" required /></label>
      <div class="setting-row"><label for="runtime-database-path"><strong>供应商数据源</strong><small>默认只读加载 CC Switch 数据库</small></label><div class="setting-control-with-action"><input id="runtime-database-path" v-model.trim="settings.database_path" type="text" spellcheck="false" autocomplete="off" required /><button class="secondary-button setting-action-button" type="button" @click="validateDatabase">验证</button></div></div>
      <div class="setting-row"><label for="runtime-health-url"><strong>服务器检测地址</strong><small>公开只读检测接口；留空则不显示服务器检测数据</small></label><div class="setting-control-with-action"><input id="runtime-health-url" v-model.trim="settings.health_status_url" type="url" inputmode="url" spellcheck="false" autocomplete="off" placeholder="https://status.example.com/api/status" /><button class="secondary-button setting-action-button" type="button" @click="testHealthUrl">测试</button></div></div>
      <label v-if="config.features?.provider_launch_command" class="setting-row setting-toggle"><span><strong>供应商临时启动命令</strong><small>在供应商卡片中显示“复制临时启动命令”按钮</small></span><input v-model="settings.show_provider_launch_command" type="checkbox" /></label>
      <div class="setting-row setting-readonly-row"><span><strong>本地数据目录</strong><small>共享设置与两套协议数据均保存在此，不保存供应商 Key</small></span><div class="setting-path-value"><code>{{ settings.data_directory || '—' }}</code><button class="secondary-button setting-action-button" type="button" @click="copyPath">复制路径</button></div></div>
      <div class="setting-row setting-readonly-row"><span><strong>{{ config.config_location_label || '客户端配置文件' }}</strong><small>{{ config.config_location_hint || '配置片段的默认位置' }}</small></span><code>{{ settings.codex_config_file || config.config_location || '—' }}</code></div>
      <div v-if="settings.restart_required" class="setting-notice">{{ config.restart_config_text || '端口将在退出并重新启动本地中转后生效。' }}</div>
      <div v-if="message" class="setting-notice">{{ message }}</div>
      <div class="settings-actions"><span>{{ runtimeSummary }}</span><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中...' : '保存设置' }}</button></div>
    </form>
    <div class="settings-form update-panel">
      <div class="setting-row setting-readonly-row"><span><strong>版本与更新</strong><small>{{ update.hint || '检测最新发布版本' }}</small></span><div class="setting-control-with-action"><code>{{ update.current_version || '—' }}</code><button class="secondary-button setting-action-button" type="button" @click="checkUpdate">检查更新</button><button v-if="update.update_available" class="primary-button setting-action-button" type="button" @click="applyUpdate">更新并重启</button></div></div>
      <div v-if="updateMessage" class="setting-notice">{{ updateMessage }}</div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['launch-command-visibility-change'])
const settings = ref({ configured_port: 17890, active_port: 17890, database_path: '', health_status_url: '', show_provider_launch_command: true })
const update = ref({})
const saving = ref(false)
const message = ref('')
const updateMessage = ref('')
const runtimeSummary = computed(() => Number(settings.value.configured_port) !== Number(settings.value.active_port) ? `当前使用 ${settings.value.active_port}，重启后切换到 ${settings.value.configured_port}` : '数据源和检测地址保存后即时生效')
function syncLaunchCommandVisibility() { emit('launch-command-visibility-change', settings.value.show_provider_launch_command !== false) }
async function loadSettings() { try { settings.value = { ...settings.value, ...(await controlFetch('/api/runtime-settings')) }; settings.value.health_status_url ||= ''; syncLaunchCommandVisibility() } catch (error) { message.value = `读取失败：${error.message}` }; try { update.value = await controlFetch('/api/update') } catch {} }
async function validateDatabase() { try { const result = await controlFetch('/api/runtime-settings/validate-database', jsonOptions('POST', { database_path: settings.value.database_path })); settings.value.database_path = result.database_path; message.value = `数据来源可用，已读取 ${result.provider_count} 个供应商。` } catch (error) { message.value = `数据来源不可用：${error.message}` } }
async function testHealthUrl() { if (!settings.value.health_status_url) { message.value = '检测地址为空，保存后将关闭服务器检测数据。'; return }; try { const response = await fetch(settings.value.health_status_url, { cache: 'no-store', mode: 'cors' }); if (!response.ok) throw new Error(`HTTP ${response.status}`); const payload = await response.json(); if (!Array.isArray(payload.providers)) throw new Error('检测数据格式无效'); message.value = `检测地址可用，接口返回 ${payload.providers.length} 个供应商状态。` } catch (error) { message.value = `检测地址不可用：${error.message}` } }
async function saveSettings() { saving.value = true; message.value = ''; try { settings.value = await controlFetch('/api/runtime-settings', jsonOptions('POST', { port: Number(settings.value.configured_port), database_path: settings.value.database_path, health_status_url: settings.value.health_status_url || null, ...(props.config.features?.provider_launch_command ? { show_provider_launch_command: settings.value.show_provider_launch_command } : {}) })); settings.value.health_status_url ||= ''; syncLaunchCommandVisibility(); message.value = settings.value.restart_required ? '设置已保存，重启后使用新端口。' : '运行设置已保存。' } catch (error) { message.value = `保存失败：${error.message}` } finally { saving.value = false } }
async function copyPath() { await navigator.clipboard.writeText(settings.value.data_directory || '') ; message.value = '本地数据目录已复制。' }
async function checkUpdate() { try { update.value = await controlFetch('/api/update/check', { method: 'POST' }); updateMessage.value = update.value.update_available ? `发现新版本 ${update.value.latest_version || ''}` : '当前已经是最新版本。' } catch (error) { updateMessage.value = `检查失败：${error.message}` } }
async function applyUpdate() { try { await controlFetch('/api/update/apply', { method: 'POST' }); updateMessage.value = '更新已开始，完成后将自动重启。' } catch (error) { updateMessage.value = `更新失败：${error.message}` } }
onMounted(loadSettings)
</script>
