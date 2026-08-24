<template>
  <section id="requests-view" class="requests-view view-panel" aria-labelledby="requests-title">
    <div class="view-heading">
      <h2 id="requests-title">请求记录</h2>
    </div>

    <Toolbar
      :timeWindow="timeWindow"
      :searchQuery="searchQuery"
      @update:timeWindow="timeWindow = $event"
      @update:searchQuery="searchQuery = $event"
      @refresh="loadRequests"
    />

    <div v-if="filteredRequests.length === 0" class="empty-state">
      <p>暂无请求记录</p>
    </div>

    <div v-else class="requests-table-container">
      <table class="requests-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>方法</th>
            <th>路径</th>
            <th>供应商</th>
            <th>状态</th>
            <th>耗时</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="request in filteredRequests" :key="request.id" class="request-row">
            <td>{{ formatTime(request.timestamp) }}</td>
            <td><span class="method-badge" :class="`method-${request.method.toLowerCase()}`">{{ request.method }}</span></td>
            <td class="path-cell">{{ request.path }}</td>
            <td>{{ request.provider || '-' }}</td>
            <td><span class="status-badge" :class="getStatusClass(request.status)">{{ request.status }}</span></td>
            <td>{{ request.duration ? `${request.duration}ms` : '-' }}</td>
            <td>
              <button class="icon-button small" @click="showDetails(request)" aria-label="查看详情">👁️</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Request Details Modal -->
    <div v-if="selectedRequest" class="modal-overlay" @click.self="selectedRequest = null">
      <div class="modal-content modal-large">
        <div class="modal-header">
          <h3>请求详情</h3>
          <button class="icon-button" @click="selectedRequest = null">✕</button>
        </div>
        <div class="modal-body">
          <div class="detail-section">
            <h4>基本信息</h4>
            <div class="detail-grid">
              <div><strong>时间:</strong> {{ formatTime(selectedRequest.timestamp) }}</div>
              <div><strong>方法:</strong> {{ selectedRequest.method }}</div>
              <div><strong>路径:</strong> {{ selectedRequest.path }}</div>
              <div><strong>供应商:</strong> {{ selectedRequest.provider || '-' }}</div>
              <div><strong>状态:</strong> {{ selectedRequest.status }}</div>
              <div><strong>耗时:</strong> {{ selectedRequest.duration }}ms</div>
            </div>
          </div>
          <div class="detail-section" v-if="selectedRequest.request_headers">
            <h4>请求头</h4>
            <pre><code>{{ JSON.stringify(selectedRequest.request_headers, null, 2) }}</code></pre>
          </div>
          <div class="detail-section" v-if="selectedRequest.response_headers">
            <h4>响应头</h4>
            <pre><code>{{ JSON.stringify(selectedRequest.response_headers, null, 2) }}</code></pre>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import Toolbar from './Toolbar.vue'

const requests = ref([])
const timeWindow = ref('1h')
const searchQuery = ref('')
const selectedRequest = ref(null)

const filteredRequests = computed(() => {
  let filtered = requests.value

  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    filtered = filtered.filter(r =>
      r.path?.toLowerCase().includes(query) ||
      r.provider?.toLowerCase().includes(query) ||
      r.method?.toLowerCase().includes(query)
    )
  }

  return filtered
})

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

function getStatusClass(status) {
  if (!status) return 'status-unknown'
  if (status >= 200 && status < 300) return 'status-success'
  if (status >= 400 && status < 500) return 'status-client-error'
  if (status >= 500) return 'status-server-error'
  return 'status-unknown'
}

function showDetails(request) {
  selectedRequest.value = request
}

async function loadRequests() {
  try {
    const response = await fetch(`/control/requests?window=${timeWindow.value}`)
    if (response.ok) {
      requests.value = await response.json()
    }
  } catch (error) {
    console.error('Failed to load requests:', error)
  }
}

onMounted(() => {
  loadRequests()
  setInterval(loadRequests, 10000) // Auto-refresh every 10s
})
</script>
