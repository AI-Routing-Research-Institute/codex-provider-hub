<template>
  <button
    class="provider-row"
    :class="{
      current: isCurrent,
      managing: manageMode
    }"
    type="button"
    @click="$emit('select', provider.id)"
  >
    <div class="provider-state">
      <span class="dot"></span>
      <span>{{ statusText }}</span>
    </div>

    <div class="provider-copy">
      <div class="provider-title">
        <button class="provider-select" type="button" :disabled="manageMode">
          <strong>{{ provider.name }}</strong>
        </button>
      </div>
      <code>{{ provider.base_url }}</code>
    </div>

    <div class="provider-request-cell">
      <span class="provider-request-empty">—</span>
    </div>

    <div class="provider-health-cell">
      <div class="provider-health-summary">
        <div class="provider-health-top">
          <div class="provider-health-label">
            <span class="provider-health-dot unknown"></span>
            <span>未知</span>
          </div>
        </div>
      </div>
    </div>

    <div class="provider-token-cell">
      <span class="provider-token-zero">—</span>
    </div>

    <div class="provider-meta">
      <span class="auth-label" :class="{ missing: !provider.has_key }">
        {{ provider.has_key ? '已配置' : '缺少 Key' }}
      </span>
    </div>
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  provider: {
    type: Object,
    required: true
  },
  isCurrent: {
    type: Boolean,
    default: false
  },
  manageMode: {
    type: Boolean,
    default: false
  }
})

defineEmits(['select'])

const statusText = computed(() => {
  return props.isCurrent ? '当前' : '可选'
})
</script>
