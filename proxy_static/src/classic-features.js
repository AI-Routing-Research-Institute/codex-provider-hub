import { createApp, h, onBeforeUnmount, onMounted, ref, Teleport } from 'vue'
import UsageTrendView from './components/UsageTrendView.vue'
import ShareCardDialog from './components/ShareCardDialog.vue'
import './classic-features.css'

export function classicUsageState(root = document) {
  const tab = root.querySelector('[data-view="usage"]')
  const panel = root.querySelector('#classic-usage-view')
  return { enabled: Boolean(tab && !tab.hidden), active: Boolean(tab && !tab.hidden && panel && !panel.hidden) }
}

export function mountClassicFeatures(root = document, events = window) {
  const host = root.querySelector('#classic-features-host')
  const shareButton = root.querySelector('#usage-share-button')
  if (!host || !shareButton) return null
  const app = createApp({
    setup() {
      const active = ref(false)
      const shareOpen = ref(false)
      let returnFocus = null
      function closeShare() {
        shareOpen.value = false
        if (returnFocus?.isConnected && !returnFocus.hidden) returnFocus.focus()
      }
      function syncView() {
        const state = classicUsageState(root)
        active.value = state.active
        if (!state.enabled || !root.querySelector('#providers-view') || root.querySelector('#providers-view').hidden) closeShare()
      }
      function openShare() {
        if (!classicUsageState(root).enabled || shareButton.hidden) return
        returnFocus = shareButton
        shareOpen.value = true
      }
      onMounted(() => {
        syncView()
        events.addEventListener('local-proxy:classic-view-change', syncView)
        shareButton.addEventListener('click', openShare)
        shareButton.disabled = false
        host.dataset.ready = 'true'
        root.querySelector('.classic-feature-loading')?.remove()
      })
      onBeforeUnmount(() => {
        events.removeEventListener('local-proxy:classic-view-change', syncView)
        shareButton.removeEventListener('click', openShare)
        shareButton.disabled = true
      })
      return () => [
        active.value ? h(Teleport, { to: '#classic-usage-view' }, [h(UsageTrendView)]) : null,
        shareOpen.value ? h(ShareCardDialog, { onClose: closeShare }) : null
      ]
    }
  })
  app.mount(host)
  return app
}

mountClassicFeatures()
