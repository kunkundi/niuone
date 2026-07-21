<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  summary: { type: Object, required: true },
  generating: Boolean,
})
const emit = defineEmits(['generate'])
const expanded = ref(false)

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
const staleText = computed(() => {
  if (!props.summary.stale) return ''
  const reasons = Array.isArray(props.summary.stale_reasons)
    ? props.summary.stale_reasons.filter(Boolean)
    : []
  return ` · ${reasons.join('、') || '盘面资料已更新'}，建议重新生成`
})
const statusText = computed(() => props.summary.loading
  ? '正在读取今日盘面扫描'
  : (scanCount.value ? `复盘资料：${sourceCountText.value}` : '今日暂无A股盘面扫描'))
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

watch(() => props.summary.generated_at, () => { expanded.value = false })
</script>

<template>
  <div class="practice-market-summary-action">
    <button
      type="button"
      class="practice-market-summary-btn"
      :disabled="generating"
      :aria-busy="generating ? 'true' : undefined"
      @click="emit('generate')"
    >{{ generating ? '处理中 · 正在抓取实时盘面并对比…' : '生成今日盘面总结' }}</button>
    <span>{{ statusText }}{{ staleText }}</span>
  </div>
  <div v-if="summary.error" class="practice-market-summary-error">{{ summary.error }}</div>
  <section
    v-if="summary.available && summary.summary"
    class="practice-market-summary-card"
    :class="[{ open: expanded, collapsed: !expanded, stale: summary.stale }]"
  >
    <button type="button" class="practice-market-summary-head" :aria-expanded="expanded" @click="expanded = !expanded">
      <span class="practice-market-summary-title">今日盘面总结 · {{ summary.tone_label || '中性' }}</span>
      <span class="practice-market-summary-compact-meta">{{ sourceCountText }} · {{ String(summary.generated_at || '').slice(5, 16) }}</span>
      <span class="practice-market-summary-chevron" aria-hidden="true">›</span>
    </button>
    <div v-show="expanded" class="practice-market-summary-body">
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
</template>
