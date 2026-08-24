<template>
  <div id="app" class="app-container">
    <Titlebar />
    <ConnectionStrip />
    <ViewTabs :activeView="activeView" @changeView="activeView = $event" />

    <main class="main-content">
      <ProvidersView v-if="activeView === 'providers'" />
      <RequestsView v-else-if="activeView === 'requests'" />
      <SettingsView v-else-if="activeView === 'settings'" />
      <RuntimeView v-else-if="activeView === 'runtime'" />
      <MonitorView v-else-if="activeView === 'monitor'" />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import Titlebar from './components/Titlebar.vue'
import ConnectionStrip from './components/ConnectionStrip.vue'
import ViewTabs from './components/ViewTabs.vue'
import ProvidersView from './components/ProvidersView.vue'
import RequestsView from './components/RequestsView.vue'
import SettingsView from './components/SettingsView.vue'
import RuntimeView from './components/RuntimeView.vue'
import MonitorView from './components/MonitorView.vue'

const activeView = ref('providers')

onMounted(() => {
  // Initialize theme from localStorage
  const savedTheme = localStorage.getItem('local-proxy-theme') || 'system'
  const dark = savedTheme === 'dark' || (savedTheme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.themePreference = savedTheme
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
})
</script>
