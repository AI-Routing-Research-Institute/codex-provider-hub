<template>
  <nav class="view-tabs" role="tablist" aria-label="控制台页面">
    <button v-for="tab in visibleTabs" :key="tab.id" class="view-tab" :class="{ active: activeView === tab.id }" type="button" role="tab" :aria-selected="activeView === tab.id" :aria-controls="`${tab.id}-view`" @click="$emit('changeView', tab.id)">
      {{ tab.label }} <span v-if="tab.id === 'requests' && requestCount" class="view-tab-count">{{ requestCount }}</span>
    </button>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ activeView: { type: String, required: true }, requestCount: { type: Number, default: 0 }, requestsEnabled: { type: Boolean, default: true } })
defineEmits(['changeView'])
const tabs = [{ id: 'providers', label: '供应商' }, { id: 'requests', label: '请求' }, { id: 'settings', label: '重试设置' }, { id: 'runtime', label: '运行设置' }, { id: 'monitor', label: '监控管理' }]
const visibleTabs = computed(() => tabs.filter(tab => tab.id !== 'requests' || props.requestsEnabled))
</script>
