<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  summary: { type: Object, required: true },
  generating: Boolean,
})
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

function compactMarketEvaluation(value, comparisonLines = []) {
  const sentences = String(value || '')
    .match(/[^。！？!?]+[。！？!?]?/g)
    ?.map(sentence => sentence.trim())
    .filter(Boolean) || []
  const isContextOnly = sentence => [
    /前一美股交易日/,
    /已汇总\s*\d+\s*次A股盘面扫描/,
    /^实时快照显示[：:]/,
  ].some(pattern => pattern.test(sentence))
  const conclusion = sentences.find(sentence => !isContextOnly(sentence)
    && /(全市场|盘面|指数|情绪|资金|板块|风险|涨跌|赚钱效应|亏钱效应)/.test(sentence))
  return conclusion
    || sentences.find(sentence => !isContextOnly(sentence))
    || sentences[0]
    || (Array.isArray(comparisonLines) ? comparisonLines[0] : '')
    || '已更新'
}

const evaluationText = computed(() => compactMarketEvaluation(
  props.summary.summary,
  props.summary.comparison_lines,
))
const evaluationTime = computed(() => String(
  props.summary.generated_at || props.summary.live_snapshot_at || '',
).slice(5, 16))
const staleText = computed(() => {
  if (!props.summary.stale) return ''
  const reasons = Array.isArray(props.summary.stale_reasons)
    ? props.summary.stale_reasons.filter(Boolean)
    : []
  return `${reasons.join('、') || '盘面资料已更新'}，建议重新生成`
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
  <div
    v-if="summary.available && summary.summary"
    class="practice-market-evaluation"
    :class="{ stale: summary.stale }"
    :title="summary.stale ? staleText : undefined"
  >
    <span class="practice-market-evaluation-label">盘面评价</span>
    <span class="practice-market-evaluation-tone">{{ summary.tone_label || '中性' }}</span>
    <span class="practice-market-evaluation-text">{{ evaluationText }}</span>
    <time>{{ evaluationTime }}</time>
    <button
      ref="viewButton"
      type="button"
      class="practice-market-summary-view-btn"
      aria-haspopup="dialog"
      @click="openDialog"
    >查看详情</button>
  </div>
  <div v-else class="practice-market-summary-empty">{{ statusText }}</div>
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
