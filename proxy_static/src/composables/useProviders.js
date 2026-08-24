import { ref } from 'vue'

const CONTROL_BASE = (() => {
  const pathname = window.location?.pathname || '/control/codex/'
  const match = pathname.match(/^\/control\/(codex|claude)(?:\/|$)/)
  return match ? `/control/${match[1]}` : '/control/codex'
})()

function controlUrl(path) {
  return `${CONTROL_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

export function useProviders() {
  const providers = ref([])
  const currentProviderId = ref(null)
  const loading = ref(false)
  const error = ref(null)

  async function fetchProviders() {
    loading.value = true
    error.value = null

    try {
      const response = await fetch(controlUrl('/api/status'), {
        headers: { 'X-Local-Proxy-Control': '1' }
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()

      providers.value = data.providers || []
      currentProviderId.value = data.current_provider_id || null

    } catch (err) {
      error.value = err.message
      console.error('Failed to fetch providers:', err)
    } finally {
      loading.value = false
    }
  }

  async function selectProvider(providerId) {
    try {
      const response = await fetch(controlUrl('/api/select'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Local-Proxy-Control': '1'
        },
        body: JSON.stringify({ provider_id: providerId })
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      currentProviderId.value = providerId

    } catch (err) {
      error.value = err.message
      console.error('Failed to select provider:', err)
    }
  }

  return {
    providers,
    currentProviderId,
    loading,
    error,
    fetchProviders,
    selectProvider
  }
}
