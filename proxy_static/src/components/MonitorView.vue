<template>
  <section id="monitor-view" class="monitor-view view-panel" aria-labelledby="monitor-title">
    <div class="view-heading">
      <h2 id="monitor-title">监控</h2>
      <button class="secondary-button" @click="loadMonitorData">刷新</button>
    </div>

    <div class="monitor-content">
      <div class="stat-cards">
        <div class="stat-card">
          <div class="stat-icon">📊</div>
          <div class="stat-details">
            <div class="stat-value">{{ stats.totalRequests }}</div>
            <div class="stat-label">总请求数</div>
          </div>
        </div>

        <div class="stat-card">
          <div class="stat-icon">✅</div>
          <div class="stat-details">
            <div class="stat-value">{{ stats.successRate }}%</div>
            <div class="stat-label">成功率</div>
          </div>
        </div>

        <div class="stat-card">
          <div class="stat-icon">⏱️</div>
          <div class="stat-details">
            <div class="stat-value">{{ stats.avgDuration }}ms</div>
            <div class="stat-label">平均耗时</div>
          </div>
        </div>

        <div class="stat-card">
          <div class="stat-icon">⚠️</div>
          <div class="stat-details">
            <div class="stat-value">{{ stats.errorCount }}</div>
            <div class="stat-label">错误数</div>
          </div>
        </div>
      </div>

      <div class="monitor-section">
        <h3>活跃供应商</h3>
        <div v-if="activeProviders.length === 0" class="empty-state">
          <p>暂无活跃供应商</p>
        </div>
        <div v-else class="provider-list">
          <div v-for="provider in activeProviders" :key="provider.name" class="provider-monitor-card">
            <div class="provider-monitor-header">
              <span class="provider-monitor-name">{{ provider.name }}</span>
              <span class="provider-status-dot" :class="provider.healthy ? 'status-healthy' : 'status-unhealthy'"></span>
            </div>
            <div class="provider-monitor-stats">
              <div class="monitor-stat-item">
                <span class="monitor-stat-label">请求数</span>
                <span class="monitor-stat-value">{{ provider.requestCount }}</span>
              </div>
              <div class="monitor-stat-item">
                <span class="monitor-stat-label">成功率</span>
                <span class="monitor-stat-value">{{ provider.successRate }}%</span>
              </div>
              <div class="monitor-stat-item">
                <span class="monitor-stat-label">平均耗时</span>
                <span class="monitor-stat-value">{{ provider.avgDuration }}ms</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="monitor-section">
        <h3>最近错误</h3>
        <div v-if="recentErrors.length === 0" class="empty-state">
          <p>暂无错误记录</p>
        </div>
        <div v-else class="error-list">
          <div v-for="error in recentErrors" :key="error.id" class="error-card">
            <div class="error-time">{{ formatTime(error.timestamp) }}</div>
            <div class="error-provider">{{ error.provider }}</div>
            <div class="error-message">{{ error.message }}</div>
            <div class="error-status">{{ error.status }}</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const stats = ref({
  totalRequests: 0,
  successRate: 0,
  avgDuration: 0,
  errorCount: 0
})

const activeProviders = ref([])
const recentErrors = ref([])

function formatTime(timestamp) {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

async function loadMonitorData() {
  try {
    // Load overall stats
    const statusResponse = await fetch('/control/status')
    if (statusResponse.ok) {
      const statusData = await statusResponse.json()
      const usage = statusData.usage || {}

      stats.value = {
        totalRequests: usage.total_requests || 0,
        successRate: usage.success_rate ? Math.round(usage.success_rate * 100) : 0,
        avgDuration: usage.avg_duration ? Math.round(usage.avg_duration) : 0,
        errorCount: usage.error_count || 0
      }
    }

    // Load provider stats
    const providersResponse = await fetch('/control/providers')
    if (providersResponse.ok) {
      const providers = await providersResponse.json()
      activeProviders.value = providers
        .filter(p => p.enabled)
        .map(p => ({
          name: p.name,
          healthy: p.healthy !== false,
          requestCount: p.request_count || 0,
          successRate: p.success_rate ? Math.round(p.success_rate * 100) : 0,
          avgDuration: p.avg_duration ? Math.round(p.avg_duration) : 0
        }))
    }

    // Load recent errors from request history
    const requestsResponse = await fetch('/control/requests?window=1h')
    if (requestsResponse.ok) {
      const requests = await requestsResponse.json()
      recentErrors.value = requests
        .filter(r => r.status >= 400)
        .slice(0, 10)
        .map(r => ({
          id: r.id,
          timestamp: r.timestamp,
          provider: r.provider || '-',
          message: r.error_message || `HTTP ${r.status}`,
          status: r.status
        }))
    }
  } catch (error) {
    console.error('Failed to load monitor data:', error)
  }
}

onMounted(() => {
  loadMonitorData()
  setInterval(loadMonitorData, 30000) // Auto-refresh every 30s
})
</script>
