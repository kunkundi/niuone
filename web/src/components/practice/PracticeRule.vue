<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  practice: { type: Object, required: true },
  fullSnapshotStatus: { type: String, default: 'idle' },
})
const open = ref(false)
const fallbackNote = '100股整数倍、T+1；09:15-09:25只作开盘集合竞价观察，09:25-09:30不模拟成交。'
const note = computed(() => String(props.practice.trade_rule_note || fallbackNote).trim())
const ruleMeta = computed(() => {
  const quote = props.practice.last_quote_refresh || {}
  const channels = quote.channel_counts || {}
  const singleRetryCount = Math.max(0, Math.trunc(Number(channels.single) || 0))
  const quoteText = quote.quote_time
    ? `行情：${quote.quote_time} 更新${quote.updated ?? 0}只 腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}${singleRetryCount ? `，单股重试${singleRetryCount}只` : ''}${quote.fallback ? `，回退${quote.fallback}只` : ''}`
    : ''
  const model = String(props.practice.decision_model || '').trim()
  return [`模型：${model || (props.fullSnapshotStatus === 'error' ? '未知' : '加载中')}`, quoteText].filter(Boolean).join('｜')
})
function close() { open.value = false }
function handleKeydown(event) { if (open.value && event.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <div class="practice-rule-row"><button type="button" class="practice-rule-btn" @click="open = true">交易规则</button><span class="practice-rule-meta">{{ ruleMeta }}</span></div>
  <Teleport to="body">
    <div v-if="open" class="practice-rule-backdrop" role="presentation" @click.self="close">
      <div class="practice-rule-card" role="dialog" aria-modal="true" aria-label="交易规则"><div class="practice-rule-head"><div class="practice-rule-title">交易规则</div><button type="button" class="practice-rule-close" title="关闭" aria-label="关闭" @click="close">x</button></div><div class="practice-rule-body">{{ note }}</div></div>
    </div>
  </Teleport>
</template>
