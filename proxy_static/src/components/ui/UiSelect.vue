<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import UiIcon from './UiIcon.vue'

let nextId = 0

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
  options: { type: Array, default: () => [] },
  ariaLabel: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: '' },
  placeholder: { type: String, default: '请选择' }
})
const emit = defineEmits(['update:modelValue', 'change'])

const root = ref(null)
const trigger = ref(null)
const menu = ref(null)
const optionRefs = ref([])
const open = ref(false)
const activeIndex = ref(-1)
const menuStyle = ref({})
const listId = `ui-select-${++nextId}`

const normalizedOptions = computed(() => props.options.map(option => {
  if (option && typeof option === 'object') return { value: option.value, label: option.label, disabled: option.disabled === true }
  return { value: option, label: String(option), disabled: false }
}))
const selectedOption = computed(() => normalizedOptions.value.find(option => option.value === props.modelValue))
const selectedLabel = computed(() => selectedOption.value?.label ?? props.placeholder)

function setOpen(value) {
  if (props.disabled) return
  open.value = value
  if (value) {
    activeIndex.value = Math.max(0, normalizedOptions.value.findIndex(option => option.value === props.modelValue))
    nextTick(() => {
      updateMenuPosition()
      focusActive()
    })
  } else {
    activeIndex.value = -1
  }
}
function updateMenuPosition() {
  if (!trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const menuHeight = menu.value?.offsetHeight || 240
  const gap = 4
  const top = window.innerHeight - rect.bottom < menuHeight && rect.top > menuHeight + gap
    ? rect.top - menuHeight - gap
    : rect.bottom + gap
  menuStyle.value = {
    left: `${Math.max(8, rect.left)}px`,
    top: `${Math.max(8, top)}px`,
    minWidth: `${Math.max(200, rect.width)}px`
  }
}
function focusActive() { optionRefs.value[activeIndex.value]?.focus() }
function moveActive(direction) {
  const options = normalizedOptions.value
  if (!options.length) return
  let index = activeIndex.value < 0 ? 0 : activeIndex.value
  for (let attempts = 0; attempts < options.length; attempts += 1) {
    index = (index + direction + options.length) % options.length
    if (!options[index].disabled) { activeIndex.value = index; focusActive(); return }
  }
}
function selectOption(option) {
  if (option.disabled) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
  setOpen(false)
  nextTick(() => trigger.value?.focus())
}
function onTriggerKeydown(event) {
  if (props.disabled) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) setOpen(true)
    else moveActive(event.key === 'ArrowDown' ? 1 : -1)
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    setOpen(!open.value)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    setOpen(false)
  }
}
function onOptionKeydown(event, option) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectOption(option)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    setOpen(false)
    nextTick(() => trigger.value?.focus())
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(event.key === 'ArrowDown' ? 1 : -1)
  }
}
function onDocumentPointerdown(event) {
  if (root.value && !root.value.contains(event.target) && !menu.value?.contains(event.target)) setOpen(false)
}
function onWindowChange() { if (open.value) updateMenuPosition() }
watch(() => props.disabled, value => { if (value) setOpen(false) })
onMounted(() => document.addEventListener('pointerdown', onDocumentPointerdown))
onMounted(() => {
  window.addEventListener('resize', onWindowChange)
  window.addEventListener('scroll', onWindowChange, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerdown)
  window.removeEventListener('resize', onWindowChange)
  window.removeEventListener('scroll', onWindowChange, true)
})
</script>

<template>
  <div ref="root" class="ui-select" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      ref="trigger"
      class="ui-select-trigger"
      type="button"
      :disabled="disabled"
      :aria-label="ariaLabel || undefined"
      :aria-expanded="open"
      :aria-controls="listId"
      aria-haspopup="listbox"
      @click="setOpen(!open)"
      @keydown="onTriggerKeydown"
    >
      <UiIcon v-if="icon" :name="icon" class="ui-select-icon" :size="14" />
      <span class="ui-select-value">{{ selectedLabel }}</span>
      <UiIcon name="chevronDown" class="ui-select-chevron" :size="15" />
    </button>
    <Teleport to="body">
      <div v-if="open" ref="menu" :id="listId" class="ui-select-menu" :style="menuStyle" role="listbox" :aria-label="ariaLabel || undefined" @pointerdown.stop>
        <button
          v-for="(option, index) in normalizedOptions"
          :key="String(option.value)"
          :ref="element => { optionRefs[index] = element }"
          class="ui-select-option"
          :class="{ active: option.value === modelValue, highlighted: index === activeIndex }"
          type="button"
          role="option"
          :aria-selected="option.value === modelValue"
          :disabled="option.disabled"
          @click="selectOption(option)"
          @keydown="onOptionKeydown($event, option)"
        >{{ option.label }}</button>
      </div>
    </Teleport>
  </div>
</template>
