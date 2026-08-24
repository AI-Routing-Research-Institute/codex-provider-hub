<template>
  <section id="settings-view" class="settings-view view-panel" aria-labelledby="settings-title">
    <div class="settings-heading">
      <div><h2 id="settings-title">重试设置</h2><p>设置只影响保存后的新请求</p></div>
      <span class="live-pill" :class="{ pending: !policy.enabled }"><span class="dot" />{{ policy.enabled ? '自动恢复已启用' : '自动恢复已关闭' }}</span>
    </div>
    <form class="settings-form" @submit.prevent="savePolicy">
      <label class="setting-row setting-toggle"><span><strong>自动重试</strong><small>拦截输出开始前的临时错误</small></span><input v-model="policy.enabled" type="checkbox" /></label>
      <label class="setting-row"><span><strong>最大尝试次数</strong><small>包含首次请求</small></span><UiSelect v-model="policy.max_attempts" aria-label="最大尝试次数" :options="attemptOptions" /></label>
      <label class="setting-row"><span><strong>首次等待</strong><small>失败后等待多久再次尝试</small></span><UiSelect v-model="policy.delay_seconds" aria-label="首次等待" :options="delayOptions" /></label>
      <label class="setting-row"><span><strong>等待策略</strong><small>固定间隔或逐次增加</small></span><UiSelect v-model="policy.strategy" aria-label="等待策略" :options="strategyOptions" /></label>
      <label class="setting-row"><span><strong>最大等待</strong><small>单次等待不会超过此值</small></span><UiSelect v-model="policy.max_delay_seconds" aria-label="最大等待" :options="maxDelayOptions" /></label>
      <label class="setting-row"><span><strong>熔断阈值</strong><small>连续失败多少个请求后暂停接收</small></span><UiSelect v-model="policy.circuit_failure_threshold" aria-label="熔断阈值" :options="failureThresholdOptions" /></label>
      <label class="setting-row"><span><strong>熔断时间</strong><small>暂停后自动恢复</small></span><UiSelect v-model="policy.circuit_cooldown_seconds" aria-label="熔断时间" :options="cooldownOptions" /></label>
      <div v-if="message" class="setting-notice">{{ message }}</div>
      <div class="settings-actions"><span>{{ summary }}</span><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中...' : '保存设置' }}</button></div>
    </form>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { controlFetch, jsonOptions } from '../api.js'
import UiSelect from './ui/UiSelect.vue'

const policy = ref({ enabled: true, max_attempts: 4, delay_seconds: 1, strategy: 'exponential', max_delay_seconds: 30, circuit_failure_threshold: 3, circuit_cooldown_seconds: 30 })
const withUnit = (values, unit) => values.map(value => ({ value, label: `${value} ${unit}` }))
const attemptOptions = [...withUnit([1, 2, 3, 4, 5, 10], '次'), { value: -1, label: '无限重试' }]
const delayOptions = withUnit([0.5, 1, 2, 3, 5, 10], '秒')
const strategyOptions = [{ value: 'exponential', label: '递增等待' }, { value: 'fixed', label: '固定等待' }]
const maxDelayOptions = withUnit([5, 10, 30, 60, 120], '秒')
const failureThresholdOptions = withUnit([1, 2, 3, 5, 10], '个')
const cooldownOptions = [...withUnit([10, 30, 60, 120], '秒'), { value: 300, label: '5 分钟' }]
const saving = ref(false)
const message = ref('')
const summary = computed(() => policy.value.enabled ? `${policy.value.max_attempts === -1 ? '无限重试' : `最多尝试 ${policy.value.max_attempts} 次`}，首次等待 ${policy.value.delay_seconds} 秒` : '临时错误将直接返回客户端')

async function loadPolicy() {
  try { const status = await controlFetch('/api/status'); const retry = status.retry || {}; policy.value = { ...policy.value, enabled: retry.enabled !== false, max_attempts: retry.max_attempts ?? 4, delay_seconds: retry.delay_seconds ?? 1, strategy: retry.strategy || 'exponential', max_delay_seconds: retry.max_delay_seconds ?? 30, circuit_failure_threshold: retry.circuit_failure_threshold ?? 3, circuit_cooldown_seconds: retry.circuit_cooldown_seconds ?? 30 } } catch (error) { message.value = `读取失败：${error.message}` }
}
async function savePolicy() {
  if (policy.value.max_delay_seconds < policy.value.delay_seconds) { message.value = '最大等待不能小于首次等待。'; return }
  saving.value = true; message.value = ''
  try { await controlFetch('/api/retry-policy', jsonOptions('POST', policy.value)); message.value = '重试设置已保存，新请求将使用更新后的策略。' } catch (error) { message.value = `保存失败：${error.message}` } finally { saving.value = false }
}
onMounted(loadPolicy)
</script>
