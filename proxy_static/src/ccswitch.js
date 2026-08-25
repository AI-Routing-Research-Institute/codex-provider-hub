export const DEFAULT_CC_SWITCH_CODEX_MODEL = 'gpt-5.6-sol'
export const LOCAL_PROXY_PLACEHOLDER_KEYS = {
  codex: 'local-codex-proxy',
  claude: 'local-claude-proxy'
}

export function buildCcSwitchImportDeeplink({ serviceId, endpoint, homepage, providerName }) {
  const app = serviceId === 'claude' ? 'claude' : 'codex'
  const normalizedEndpoint = String(endpoint || '').replace(/\/+$/, '')
  if (!normalizedEndpoint) throw new Error('本地中转地址不可用')

  const params = new URLSearchParams([
    ['resource', 'provider'],
    ['app', app],
    ['name', providerName || 'Codex Provider Hub'],
    ['homepage', homepage || normalizedEndpoint],
    ['endpoint', normalizedEndpoint],
    ['apiKey', LOCAL_PROXY_PLACEHOLDER_KEYS[app]],
    ['configFormat', 'json']
  ])
  if (app === 'codex') params.set('model', DEFAULT_CC_SWITCH_CODEX_MODEL)
  return `ccswitch://v1/import?${params.toString()}`
}
