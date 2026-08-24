<template>
  <div class="toolbar">
    <div class="toolbar-left">
      <label class="toolbar-item">
        <span class="toolbar-label">时间范围</span>
        <select :value="timeWindow" @change="$emit('update:timeWindow', $event.target.value)" class="toolbar-select">
          <option value="5m">最近 5 分钟</option>
          <option value="15m">最近 15 分钟</option>
          <option value="1h">最近 1 小时</option>
          <option value="6h">最近 6 小时</option>
          <option value="24h">最近 24 小时</option>
        </select>
      </label>
    </div>
    <div class="toolbar-right">
      <input
        type="search"
        :value="searchQuery"
        @input="$emit('update:searchQuery', $event.target.value)"
        placeholder="搜索..."
        class="toolbar-search"
      />
      <button class="secondary-button" @click="$emit('refresh')">刷新</button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  timeWindow: {
    type: String,
    required: true
  },
  searchQuery: {
    type: String,
    required: true
  }
})

defineEmits(['update:timeWindow', 'update:searchQuery', 'refresh'])
</script>
