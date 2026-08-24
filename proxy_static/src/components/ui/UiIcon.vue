<script setup>
defineProps({
  name: { type: String, required: true },
  size: { type: [String, Number], default: 16 },
  strokeWidth: { type: [String, Number], default: 1.7 }
})

const paths = {
  refresh: ['M20 11a8 8 0 0 0-14.9-3M4 5v3h3', 'M4 13a8 8 0 0 0 14.9 3M20 19v-3h-3'],
  settings: ['M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z', 'm19.4 15 .1.1a1.8 1.8 0 0 1-2.5 2.5l-.1-.1a1.8 1.8 0 0 0-3 1.3v.2a1.8 1.8 0 0 1-3.6 0v-.2a1.8 1.8 0 0 0-3-1.3l-.1.1a1.8 1.8 0 0 1-2.5-2.5l.1-.1a1.8 1.8 0 0 0-1.3-3h-.2a1.8 1.8 0 0 1 0-3h.2a1.8 1.8 0 0 0 1.3-3l-.1-.1a1.8 1.8 0 0 1 2.5-2.5l.1.1a1.8 1.8 0 0 0 3-1.3v-.2a1.8 1.8 0 0 1 3.6 0v.2a1.8 1.8 0 0 0 3 1.3l.1-.1a1.8 1.8 0 0 1 2.5 2.5l-.1.1a1.8 1.8 0 0 0 1.3 3h.2a1.8 1.8 0 0 1 0 3h-.2a1.8 1.8 0 0 0-1.3 3Z'],
  calendar: ['M7 3v3M17 3v3M4 9h16', 'M5 5h14a1 1 0 0 1 1 1v13H4V6a1 1 0 0 1 1-1Z'],
  search: ['m21 21-4.3-4.3', 'M10.8 18a7.2 7.2 0 1 0 0-14.4 7.2 7.2 0 0 0 0 14.4Z'],
  close: ['M6 6l12 12M18 6 6 18'],
  plus: ['M12 5v14M5 12h14'],
  grip: ['M8 6h.01M8 12h.01M8 18h.01M16 6h.01M16 12h.01M16 18h.01'],
  info: ['M12 11v5', 'M12 8h.01', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z'],
  clock: ['M12 7v5l3 2', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z'],
  sun: ['M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4', 'M16.5 12a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z'],
  moon: ['M20.5 15.5A8.5 8.5 0 0 1 8.5 3.5 8.5 8.5 0 1 0 20.5 15.5Z'],
  monitor: ['M4 5h16v11H4z', 'M8 20h8M12 16v4'],
  chevronDown: ['m7 10 5 5 5-5']
}
</script>

<template>
  <svg class="ui-icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" :stroke-width="strokeWidth" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path v-for="(path, index) in (paths[name] || [])" :key="index" :d="path" />
  </svg>
</template>
