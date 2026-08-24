<template>
  <div v-if="open" class="time-range-popover" role="dialog" aria-modal="false" aria-labelledby="time-range-title" style="top: 112px; left: max(12px, calc(50vw - 190px));">
    <div class="time-range-heading"><div><strong id="time-range-title">{{ title }}</strong><span>精确到秒</span></div><button class="usage-history-close" type="button" aria-label="关闭自定义时间" @click="$emit('close')"><UiIcon name="close" /></button></div>
    <div class="time-range-fields">
      <fieldset><legend>开始时间</legend><div><input v-model="startDate" type="date" aria-label="开始日期" /><input v-model="startTime" type="time" step="1" aria-label="开始时间" /></div></fieldset>
      <fieldset><legend>结束时间</legend><div><input v-model="endDate" type="date" aria-label="结束日期" /><input v-model="endTime" type="time" step="1" aria-label="结束时间" /></div></fieldset>
    </div>
    <p v-if="error" class="time-range-error" role="alert">{{ error }}</p>
    <div class="time-range-actions"><button class="secondary-button" type="button" @click="$emit('close')">取消</button><button class="primary-button" type="button" @click="apply">应用</button></div>
  </div>
</template>

<script setup>
import UiIcon from './ui/UiIcon.vue'
import { ref, watch } from 'vue'
const props = defineProps({ open: { type: Boolean, default: false }, title: { type: String, default: '自定义时间范围' }, initialRange: { type: Object, default: null } })
const emit = defineEmits(['close', 'apply'])
const startDate = ref('')
const startTime = ref('00:00:00')
const endDate = ref('')
const endTime = ref('23:59:59')
const error = ref('')
function localParts(value) { const date = new Date(value); const pad = number => String(number).padStart(2, '0'); return { date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`, time: `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` } }
watch(() => props.open, (open) => { if (!open) return; const end = props.initialRange?.endAt || Date.now(); const start = props.initialRange?.startAt || end - 24 * 60 * 60 * 1000; const from = localParts(start); const to = localParts(end); startDate.value = from.date; startTime.value = from.time; endDate.value = to.date; endTime.value = to.time; error.value = '' }, { immediate: true })
function apply() { const startAt = new Date(`${startDate.value}T${startTime.value}`).getTime(); const endAt = new Date(`${endDate.value}T${endTime.value}`).getTime(); if (!Number.isFinite(startAt) || !Number.isFinite(endAt)) { error.value = '请填写完整时间。'; return } if (startAt >= endAt) { error.value = '结束时间必须晚于开始时间。'; return } emit('apply', { startAt, endAt }) }
</script>
