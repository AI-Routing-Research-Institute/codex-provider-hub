<template>
  <header class="titlebar">
    <div class="titlebar-left">
      <h1 class="titlebar-heading">本地中转控制台</h1>
    </div>
    <div class="titlebar-right">
      <button class="theme-toggle icon-button" @click="toggleTheme" :aria-label="themeLabel">
        <span v-if="theme === 'light'">🌙</span>
        <span v-else-if="theme === 'dark'">☀️</span>
        <span v-else>🌓</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { ref, computed } from 'vue'

const theme = ref('system')

const themeLabel = computed(() => {
  if (theme.value === 'light') return '切换到深色模式'
  if (theme.value === 'dark') return '切换到浅色模式'
  return '切换到系统模式'
})

function toggleTheme() {
  const modes = ['light', 'dark', 'system']
  const current = modes.indexOf(theme.value)
  const next = modes[(current + 1) % modes.length]
  theme.value = next

  localStorage.setItem('local-proxy-theme', next)

  const dark = next === 'dark' || (next === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themePreference = next
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

// Initialize from localStorage
const savedTheme = localStorage.getItem('local-proxy-theme') || 'system'
theme.value = savedTheme
</script>
