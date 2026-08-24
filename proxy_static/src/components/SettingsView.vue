<template>
  <section id="settings-view" class="settings-view view-panel" aria-labelledby="settings-title">
    <div class="view-heading">
      <h2 id="settings-title">设置</h2>
    </div>

    <div class="settings-content">
      <div class="setting-section">
        <h3>通用设置</h3>
        <div class="setting-item">
          <label class="setting-label">
            <span>主题模式</span>
            <select v-model="themeMode" @change="saveTheme" class="setting-select">
              <option value="system">跟随系统</option>
              <option value="light">浅色模式</option>
              <option value="dark">深色模式</option>
            </select>
          </label>
        </div>
        <div class="setting-item">
          <label class="setting-label">
            <span>自动刷新间隔</span>
            <select v-model="refreshInterval" @change="saveRefreshInterval" class="setting-select">
              <option :value="5000">5 秒</option>
              <option :value="10000">10 秒</option>
              <option :value="30000">30 秒</option>
              <option :value="60000">60 秒</option>
              <option :value="0">关闭</option>
            </select>
          </label>
        </div>
      </div>

      <div class="setting-section">
        <h3>关于</h3>
        <div class="about-info">
          <p><strong>Codex 本地中转控制台</strong></p>
          <p>版本: {{ version }}</p>
          <p>用于管理和监控本地 API 中转服务</p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const themeMode = ref('system')
const refreshInterval = ref(10000)
const version = ref('1.0.0')

function saveTheme() {
  localStorage.setItem('local-proxy-theme', themeMode.value)
  const dark = themeMode.value === 'dark' || (themeMode.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themePreference = themeMode.value
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

function saveRefreshInterval() {
  localStorage.setItem('local-proxy-refresh-interval', String(refreshInterval.value))
}

onMounted(() => {
  const savedTheme = localStorage.getItem('local-proxy-theme') || 'system'
  themeMode.value = savedTheme

  const savedInterval = localStorage.getItem('local-proxy-refresh-interval')
  if (savedInterval !== null) {
    refreshInterval.value = parseInt(savedInterval, 10)
  }
})
</script>
