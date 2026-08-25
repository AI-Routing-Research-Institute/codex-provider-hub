<template>
  <div class="theme-control">
    <button
      id="theme-button"
      class="theme-button"
      type="button"
      title="选择主题"
      aria-label="选择主题"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      @click="toggleMenu"
    >
      <span id="theme-icon" aria-hidden="true">{{ themeIcon }}</span>
    </button>
    <div
      id="theme-menu"
      class="theme-menu"
      role="menu"
      aria-label="主题"
      :hidden="!menuOpen"
    >
      <button
        type="button"
        role="menuitemradio"
        data-theme-value="system"
        :aria-checked="theme === 'system'"
        @click="setTheme('system')"
      >
        跟随系统
      </button>
      <button
        type="button"
        role="menuitemradio"
        data-theme-value="light"
        :aria-checked="theme === 'light'"
        @click="setTheme('light')"
      >
        浅色
      </button>
      <button
        type="button"
        role="menuitemradio"
        data-theme-value="dark"
        :aria-checked="theme === 'dark'"
        @click="setTheme('dark')"
      >
        深色
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const theme = ref('system')
const menuOpen = ref(false)

const themeIcon = computed(() => {
  return theme.value === 'dark' ? '◑' : theme.value === 'light' ? '○' : '◐'
})

function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

function setTheme(value) {
  theme.value = value
  menuOpen.value = false

  localStorage.setItem('local-proxy-theme', value)

  const dark = value === 'dark' || (value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themePreference = value
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

function handleClickOutside(e) {
  if (menuOpen.value && !e.target.closest('.theme-control')) {
    menuOpen.value = false
  }
}

onMounted(() => {
  const saved = localStorage.getItem('local-proxy-theme')
  if (['system', 'light', 'dark'].includes(saved)) {
    theme.value = saved
  }
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>
