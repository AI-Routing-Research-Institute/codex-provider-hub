<template>
  <section id="runtime-view" class="runtime-view view-panel" aria-labelledby="runtime-title">
    <div class="view-heading">
      <h2 id="runtime-title">运行信息</h2>
      <button class="secondary-button" @click="loadRuntimeInfo">刷新</button>
    </div>

    <div v-if="loading" class="empty-state">
      <p>加载中...</p>
    </div>

    <div v-else class="runtime-content">
      <div class="info-card">
        <h3>服务状态</h3>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">服务状态</span>
            <span class="info-value" :class="runtimeInfo.running ? 'status-enabled' : 'status-disabled'">
              {{ runtimeInfo.running ? '运行中' : '已停止' }}
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">启动时间</span>
            <span class="info-value">{{ formatTime(runtimeInfo.startTime) }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">运行时长</span>
            <span class="info-value">{{ formatDuration(runtimeInfo.uptime) }}</span>
          </div>
        </div>
      </div>

      <div class="info-card">
        <h3>配置信息</h3>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">监听地址</span>
            <code class="info-value">{{ runtimeInfo.host }}:{{ runtimeInfo.port }}</code>
          </div>
          <div class="info-item">
            <span class="info-label">代理路径</span>
            <code class="info-value">{{ runtimeInfo.proxyPath || '/v1' }}</code>
          </div>
          <div class="info-item">
            <span class="info-label">控制台路径</span>
            <code class="info-value">{{ runtimeInfo.controlPath || '/control' }}</code>
          </div>
        </div>
      </div>

      <div class="info-card">
        <h3>系统信息</h3>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">Python 版本</span>
            <span class="info-value">{{ runtimeInfo.pythonVersion || '-' }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">平台</span>
            <span class="info-value">{{ runtimeInfo.platform || '-' }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">进程 PID</span>
            <span class="info-value">{{ runtimeInfo.pid || '-' }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const loading = ref(true)
const runtimeInfo = ref({
  running: true,
  startTime: null,
  uptime: 0,
  host: '127.0.0.1',
  port: 17890,
  proxyPath: '/v1',
  controlPath: '/control',
  pythonVersion: null,
  platform: null,
  pid: null
})

function formatTime(timestamp) {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

function formatDuration(seconds) {
  if (!seconds) return '-'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  return `${hours}h ${minutes}m ${secs}s`
}

async function loadRuntimeInfo() {
  loading.value = true
  try {
    const response = await fetch('/control/status')
    if (response.ok) {
      const data = await response.json()
      // Mock runtime info from status data
      runtimeInfo.value = {
        running: true,
        startTime: Date.now() - (data.uptime || 0) * 1000,
        uptime: data.uptime || 0,
        host: data.host || '127.0.0.1',
        port: data.port || 17890,
        proxyPath: data.proxy_path || '/v1',
        controlPath: data.control_path || '/control',
        pythonVersion: data.python_version || '-',
        platform: data.platform || '-',
        pid: data.pid || null
      }
    }
  } catch (error) {
    console.error('Failed to load runtime info:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadRuntimeInfo()
})
</script>
