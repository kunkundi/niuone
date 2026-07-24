<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  summary: { type: Object, required: true },
  generating: Boolean,
  manualRunning: Boolean,
  manualLabel: { type: String, default: '手动运行选股与交易策略' },
})
const emit = defineEmits(['generate', 'manual-cycle'])
const dialogOpen = ref(false)
const viewButton = ref(null)
const closeButton = ref(null)

const scanCount = computed(() => Math.max(0, Number(props.summary.scan_count) || 0))
const sourceParts = computed(() => {
  const parts = [`已有A股总结 ${scanCount.value} 次`]
  const additions = [
    ['us_summary_count', '前日美股'],
    ['previous_summary_count', '上一版'],
    ['live_snapshot_count', '实时快照'],
  ]
  for (const [key, label] of additions) {
    const count = Math.max(0, Number(props.summary[key]) || 0)
    if (count) parts.push(`${label} ${count} 份`)
  }
  return parts
})
const sourceCountText = computed(() => sourceParts.value.join(' · '))
const evaluationText = computed(() => String(props.summary.summary || '').trim()
  || (Array.isArray(props.summary.comparison_lines) ? props.summary.comparison_lines[0] : '')
  || '已更新')
const evaluationTime = computed(() => String(
  props.summary.generated_at || props.summary.live_snapshot_at || '',
).slice(5, 16))
const staleText = computed(() => {
  if (!props.summary.stale) return ''
  const reasons = Array.isArray(props.summary.stale_reasons)
    ? props.summary.stale_reasons.filter(Boolean)
    : []
  return ` · ${reasons.join('、') || '盘面资料已更新'}，建议重新生成`
})
const statusText = computed(() => {
  if (props.generating || props.summary.running) {
    return props.summary.stage_label || '正在生成此刻盘面总结与评价'
  }
  if (props.summary.loading) return '正在读取今日盘面扫描'
  return props.summary.available ? `复盘资料：${sourceCountText.value}` : '暂无可用盘面资料'
})
const sections = computed(() => [
  ['实时热门行业（涨幅与主力净流入交叉确认）', props.summary.hot_sector_lines, ''],
  ['实时对比结论', props.summary.comparison_lines, ''],
  ['走势脉络', props.summary.trend_lines, ''],
  ['市场结构', props.summary.structure_lines, ''],
  ['风险变化', props.summary.risk_lines, 'risk'],
].map(([title, rows, className]) => ({
  title,
  className,
  rows: Array.isArray(rows) ? rows.filter(Boolean).slice(0, title === '风险变化' ? 4 : 5) : [],
})).filter(section => section.rows.length))

function openDialog() {
  dialogOpen.value = true
  nextTick(() => closeButton.value?.focus())
}

function closeDialog({ restoreFocus = true } = {}) {
  if (!dialogOpen.value) return
  dialogOpen.value = false
  if (restoreFocus) nextTick(() => viewButton.value?.focus())
}

function handleKeydown(event) {
  if (dialogOpen.value && event.key === 'Escape') closeDialog()
}

watch(dialogOpen, open => {
  document.body.classList.toggle('practice-market-summary-dialog-open', open)
})
watch(() => props.summary.generated_at, () => closeDialog({ restoreFocus: false }))

onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => {
  document.body.classList.remove('practice-market-summary-dialog-open')
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div v-if="summary.available && summary.summary" class="practice-market-evaluation">
    <span class="practice-market-evaluation-label">盘面评价 · {{ summary.tone_label || '中性' }}</span>
    <span>{{ evaluationText }}</span>
    <time>{{ evaluationTime }}</time>
  </div>
  <div class="practice-market-summary-action">
    <div class="practice-market-summary-primary-actions">
      <button
        type="button"
        class="practice-manual-cycle-btn"
        :disabled="manualRunning"
        :aria-busy="manualRunning ? 'true' : undefined"
        :title="manualLabel"
        @click="emit('manual-cycle')"
      >{{ manualRunning ? '处理中 · ' : '' }}{{ manualLabel }}</button>
      <button
        type="button"
        class="practice-market-summary-btn"
        :disabled="generating"
        :aria-busy="generating ? 'true' : undefined"
        @click="emit('generate')"
      >{{ generating ? '正在生成盘面总结与评价…' : '生成此刻盘面总结与评价' }}</button>
    </div>
    <button
      v-if="summary.available && summary.summary"
      ref="viewButton"
      type="button"
      class="practice-market-summary-view-btn"
      aria-haspopup="dialog"
      @click="openDialog"
    >查看总结与评价</button>
    <span class="practice-market-summary-status">{{ statusText }}{{ staleText }}</span>
  </div>
  <div v-if="summary.error" class="practice-market-summary-error">{{ summary.error }}</div>

  <Teleport to="body">
    <div
      v-if="dialogOpen"
      class="practice-market-summary-backdrop"
      role="presentation"
      @click.self="closeDialog()"
    >
      <section
        class="practice-market-summary-dialog"
        :class="{ stale: summary.stale }"
        role="dialog"
        aria-modal="true"
        aria-labelledby="practiceMarketSummaryDialogTitle"
      >
        <header class="practice-market-summary-dialog-head">
          <div class="practice-market-summary-dialog-heading">
            <h2 id="practiceMarketSummaryDialogTitle">此刻盘面总结与评价 · {{ summary.tone_label || '中性' }}</h2>
            <div>{{ sourceCountText }} · {{ String(summary.generated_at || '').slice(5, 16) }}</div>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="practice-market-summary-close"
            title="关闭"
            aria-label="关闭此刻盘面总结与评价"
            @click="closeDialog()"
          >x</button>
        </header>
        <div class="practice-market-summary-body">
          <p>{{ summary.summary }}</p>
          <div v-for="section in sections" :key="section.title" class="practice-market-summary-section" :class="section.className">
            <b>{{ section.title }}</b>
            <ul><li v-for="row in section.rows" :key="row">{{ row }}</li></ul>
          </div>
          <div class="practice-market-summary-meta">
            汇总 {{ sourceCountText }} · {{ summary.model_used ? '模型综合' : '本地规则汇总' }}<template v-if="summary.live_snapshot_at"> · 实时抓取 {{ summary.live_snapshot_at.slice(11, 19) }}</template><template v-if="summary.stale"> · 当前结果未包含最新资料</template>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
