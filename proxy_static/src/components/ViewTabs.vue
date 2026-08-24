<template>
  <nav class="view-tabs" role="tablist" aria-label="视图切换">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      :class="['view-tab', { 'view-tab--active': activeView === tab.id }]"
      role="tab"
      :aria-selected="activeView === tab.id"
      :aria-controls="`${tab.id}-view`"
      @click="$emit('changeView', tab.id)"
    >
      {{ tab.label }}
    </button>
  </nav>
</template>

<script setup>
defineProps({
  activeView: {
    type: String,
    required: true
  }
})

defineEmits(['changeView'])

const tabs = [
  { id: 'providers', label: '供应商' },
  { id: 'requests', label: '请求记录' },
  { id: 'settings', label: '设置' },
  { id: 'runtime', label: '运行' },
  { id: 'monitor', label: '监控' }
]
</script>
