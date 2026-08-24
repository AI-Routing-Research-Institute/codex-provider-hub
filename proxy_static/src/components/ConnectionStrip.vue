<template>
  <div class="connection-strip" :class="connectionClass">
    <p class="connection-message">{{ connectionMessage }}</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const isConnected = ref(true)

const connectionClass = computed(() => ({
  'connection-strip--connected': isConnected.value,
  'connection-strip--disconnected': !isConnected.value
}))

const connectionMessage = computed(() =>
  isConnected.value ? '已连接到代理服务' : '未连接到代理服务'
)

onMounted(() => {
  // Check connection status periodically
  setInterval(async () => {
    try {
      const response = await fetch('/control/health', { method: 'GET' })
      isConnected.value = response.ok
    } catch {
      isConnected.value = false
    }
  }, 5000)
})
</script>
