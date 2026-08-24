<template>
  <header class="titlebar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">{{ config.brand_mark || 'CX' }}</div>
      <div class="brand-copy"><h1>{{ config.display_name || '本地中转' }}</h1></div>
    </div>
    <div class="title-actions">
      <a v-if="config.peer_console_url" class="secondary-button console-link" :href="config.peer_console_url">{{ config.peer_console_label || '切换控制台' }}</a>
      <div class="theme-control">
        <button class="theme-button" type="button" title="选择主题" aria-label="选择主题" aria-haspopup="menu" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><UiIcon :name="themeIcon" /></button>
        <div v-show="menuOpen" class="theme-menu" role="menu" aria-label="主题">
          <button v-for="option in options" :key="option.value" type="button" role="menuitemradio" :aria-checked="theme === option.value" @click="setTheme(option.value)">{{ option.label }}</button>
        </div>
      </div>
      <div class="service-state"><div class="service-copy"><strong>中转运行中</strong><span>仅监听本机</span></div><span class="live-switch" aria-label="中转运行中"><span /></span></div>
    </div>
  </header>
</template>

<script setup>
import { computed, ref } from 'vue'
import UiIcon from './ui/UiIcon.vue'
const options = [{ value: 'system', label: '跟随系统' }, { value: 'light', label: '浅色' }, { value: 'dark', label: '深色' }]
const menuOpen = ref(false)
const theme = ref(localStorage.getItem('local-proxy-theme') || 'system')
const props = defineProps({ config: { type: Object, required: true } })
const themeIcon = computed(() => ({ system: 'monitor', light: 'sun', dark: 'moon' })[theme.value])
function setTheme(value) {
  theme.value = value
  menuOpen.value = false
  const dark = value === 'dark' || (value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  localStorage.setItem('local-proxy-theme', value)
  document.documentElement.dataset.themePreference = value
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}
</script>
