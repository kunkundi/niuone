<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  normalizePracticeOperationLogs,
  practiceLogRawText,
  practiceOperationLogDate,
} from '../../utils/practiceLogs.js'

const props = defineProps({ practice: { type: Object, required: true } })
const selectedKey = ref('')
const entries = computed(() => normalizePracticeOperationLogs(props.practice))
const selected = computed(() => entries.value.find(item => item.key === selectedKey.value) || null)

function close() {
  selectedKey.value = ''
}
function handleKeydown(event) {
  if (selected.value && event.key === 'Escape') close()
}
onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <div class="practice-log-panel">
    <div class="practice-log-head"><div class="practice-log-title">操作日志</div><div class="practice-log-count">{{ practiceOperationLogDate(practice) }} · {{ entries.length }}条</div></div>
    <div class="practice-log-scroll" tabindex="0" role="region" aria-label="当日所有操作日志">
      <button v-for="item in entries" :key="item.key" type="button" class="practice-log-row" title="查看完整日志" :aria-label="`查看完整日志：${item.summary}`" @click="selectedKey = item.key">
        <div class="practice-log-time">{{ item.time.slice(11, 19) || '--' }}</div><div class="practice-log-badge" :class="item.badgeClass">{{ item.badge }}</div><div class="practice-log-main"><div class="practice-log-summary">{{ item.summary }}</div><div v-if="item.detail" class="practice-log-detail">{{ item.detail }}</div></div>
      </button>
      <div v-if="!entries.length" class="empty" style="padding:18px;font-size:13px">当日暂无操作日志</div>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="selected" class="practice-log-detail-backdrop" role="presentation" @click.self="close">
      <div class="practice-log-detail-card" role="dialog" aria-modal="true" aria-label="完整操作日志">
        <div class="practice-log-detail-head"><div class="practice-log-detail-title">{{ selected.summary || '完整操作日志' }}</div><button type="button" class="practice-log-detail-close" title="关闭" aria-label="关闭" @click="close">x</button></div>
        <div class="practice-log-detail-body"><div class="practice-log-detail-text">{{ practiceLogRawText(selected) }}</div></div>
      </div>
    </div>
  </Teleport>
</template>
