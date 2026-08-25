<template>
  <div ref="root" class="time-range-select" :class="{ 'is-open': open }">
    <button ref="trigger" class="time-range-select-trigger" type="button" :aria-label="ariaLabel" :aria-expanded="open" @click="toggle">
      <UiIcon v-if="icon" :name="icon" :size="14" />
      <span>{{ selectedLabel }}</span>
      <UiIcon name="chevronDown" :size="15" />
    </button>
    <div v-if="open" class="time-range-select-menu" :style="{ width: `${menuWidth}px` }" role="dialog" :aria-label="title" @pointerdown.stop>
      <div class="time-range-presets" role="listbox" :aria-label="ariaLabel">
        <button v-for="option in options" :key="String(option.value)" type="button" role="option" :aria-selected="option.value === pendingValue" :class="{ active: option.value === pendingValue }" @click="select(option)">{{ option.label }}</button>
      </div>
      <div class="time-range-custom">
        <div class="time-range-custom-fields">
          <label><span>开始日期</span><input v-model="startDate" type="date" aria-label="开始日期" @input="dateEdited = true" /><input v-model="startTime" type="time" step="1" aria-label="开始时间" @input="dateEdited = true" /></label>
          <span class="time-range-arrow" aria-hidden="true">→</span>
          <label><span>结束日期</span><input v-model="endDate" type="date" aria-label="结束日期" @input="dateEdited = true" /><input v-model="endTime" type="time" step="1" aria-label="结束时间" @input="dateEdited = true" /></label>
        </div>
        <p v-if="error" class="time-range-error" role="alert">{{ error }}</p>
        <div class="time-range-actions"><button class="primary-button" type="button" @click="apply">应用</button></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import UiIcon from './ui/UiIcon.vue'
import { preciseRange } from '../timeRange.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  initialRange: { type: Object, default: null },
  title: { type: String, default: '时间范围' },
  ariaLabel: { type: String, default: '时间范围' },
  icon: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue', 'change', 'apply'])
const root = ref(null)
const open = ref(false)
const startDate = ref('')
const startTime = ref('00:00:00')
const endDate = ref('')
const endTime = ref('00:00:00')
const error = ref('')
const pendingValue = ref(props.modelValue)
const dateEdited = ref(false)
const menuWidth = ref(320)
const selectedLabel = computed(() => props.options.find(option => option.value === props.modelValue)?.label || (props.modelValue === 'custom' ? '自定义时间' : '选择时间范围'))

function pad(value) { return String(value).padStart(2, '0') }
function localDate(value) {
  const date = new Date(value)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}
function localTime(value) {
  const date = new Date(value)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
function optionDates(value) {
  const hours = ({ today: 0, '1h': 1, '6h': 6, '24h': 24, '7d': 7 * 24, '30d': 30 * 24, all: 0 })[value] ?? 0
  const end = new Date()
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000)
  if (value === 'today') start.setHours(0, 0, 0, 0)
  return { start: localDate(start), startTime: localTime(start), end: localDate(end), endTime: localTime(end) }
}
function rangeDates() {
  if (props.modelValue !== 'custom') {
    const dates = optionDates(props.modelValue)
    startDate.value = dates.start
    startTime.value = dates.startTime
    endDate.value = dates.end
    endTime.value = dates.endTime
    return
  }
  const endAt = props.initialRange?.endAt || Date.now()
  const startAt = props.initialRange?.startAt || endAt - 24 * 60 * 60 * 1000
  startDate.value = localDate(startAt)
  startTime.value = localTime(startAt)
  endDate.value = localDate(endAt)
  endTime.value = localTime(endAt)
}
function toggle() { open.value ? close() : openMenu() }
function updatePlacement() {
  const rect = root.value?.getBoundingClientRect()
  if (!rect) return
  const boundary = root.value.closest('.request-filters, .toolbar-actions')?.getBoundingClientRect()
  const rightEdge = Math.min(window.innerWidth - 12, boundary?.right || window.innerWidth - 12)
  menuWidth.value = Math.min(320, Math.max(1, rightEdge - rect.left))
}
function openMenu() { pendingValue.value = props.modelValue; dateEdited.value = false; rangeDates(); error.value = ''; open.value = true; updatePlacement() }
function close() { open.value = false; error.value = '' }
function select(option) {
  // Preset dates are display-only; applying a preset emits its original window value.
  pendingValue.value = option.value
  dateEdited.value = false
  const dates = optionDates(option.value)
  startDate.value = dates.start
  startTime.value = dates.startTime
  endDate.value = dates.end
  endTime.value = dates.endTime
  error.value = ''
}
function apply() {
  if (!startDate.value || !endDate.value) { error.value = '请选择开始日期和结束日期。'; return }
  const usesPreset = pendingValue.value !== 'custom' && !dateEdited.value
  if (usesPreset) {
    emit('update:modelValue', pendingValue.value)
    emit('change', pendingValue.value)
    close()
    return
  }
  const range = preciseRange(startDate.value, startTime.value, endDate.value, endTime.value)
  if (!range) { error.value = '日期格式无效。'; return }
  if (range.startAt >= range.endAt) { error.value = '结束时间必须晚于开始时间。'; return }
  emit('update:modelValue', 'custom')
  emit('apply', range)
  close()
}
function onDocumentPointerdown(event) { if (root.value && !root.value.contains(event.target)) close() }
function onKeydown(event) { if (event.key === 'Escape') close() }
function onViewportChange() { if (open.value) updatePlacement() }
onMounted(() => { document.addEventListener('pointerdown', onDocumentPointerdown); document.addEventListener('keydown', onKeydown); window.addEventListener('resize', onViewportChange); window.addEventListener('scroll', onViewportChange, true); if (props.modelValue === 'custom') openMenu() })
onBeforeUnmount(() => { document.removeEventListener('pointerdown', onDocumentPointerdown); document.removeEventListener('keydown', onKeydown); window.removeEventListener('resize', onViewportChange); window.removeEventListener('scroll', onViewportChange, true) })
</script>
