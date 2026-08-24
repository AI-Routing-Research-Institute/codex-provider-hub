<template>
  <div class="toolbar">
    <label class="toolbar-item">
      <span class="toolbar-label">时间范围</span>
      <UiSelect :model-value="timeWindow" class="toolbar-select" aria-label="时间范围" :options="timeWindowOptions" @update:model-value="$emit('update:timeWindow', $event)" />
    </label>
    <div class="toolbar-actions">
      <label class="search-field">
        <span class="visually-hidden">搜索请求</span>
        <UiIcon class="search-icon" name="search" />
        <input
          type="search"
          :value="searchQuery"
          placeholder="搜索会话、模型或供应商"
          @input="$emit('update:searchQuery', $event.target.value)"
        />
      </label>
      <button class="secondary-button" type="button" :disabled="loading" @click="$emit('refresh')">
        {{ loading ? '刷新中...' : '刷新' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import UiSelect from './ui/UiSelect.vue'
import UiIcon from './ui/UiIcon.vue'
defineProps({
  timeWindow: { type: String, required: true },
  searchQuery: { type: String, required: true },
  loading: { type: Boolean, default: false }
})
defineEmits(['update:timeWindow', 'update:searchQuery', 'refresh'])
const timeWindowOptions = [{ value: '1h', label: '近 1 小时' }, { value: '6h', label: '近 6 小时' }, { value: '24h', label: '近 24 小时' }, { value: '7d', label: '近 7 天' }]
</script>
