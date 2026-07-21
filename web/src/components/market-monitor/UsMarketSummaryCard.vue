<script setup>
import { computed, ref } from 'vue'
import { cleanMarketLine } from '../../utils/marketMonitorDisplay.js'
import MarketSection from './MarketSection.vue'

const props = defineProps({
  summaryData: { type: Object, default: () => ({ loading: true }) },
})

const expanded = ref(false)
const loadingSummary = '这条摘要会作为今日买卖选股的外盘背景，盘中仍以 A 股竞价、资金流和板块联动确认。'
const tone = computed(() => {
  const value = String(props.summaryData?.tone || 'neutral')
  return ['offensive', 'balanced', 'cautious', 'defensive'].includes(value) ? value : 'neutral'
})
const isLoading = computed(() => props.summaryData?.loading && !props.summaryData?.generated_at)
const toneLabel = computed(() => isLoading.value ? '加载中' : (props.summaryData?.tone_label || '中性'))
const summary = computed(() => {
  if (isLoading.value) return loadingSummary
  return props.summaryData?.summary
    || (props.summaryData?.error ? '隔夜美股盘面暂不可用，今日先按 A 股自身信号执行。' : '等待隔夜美股盘面总结。')
})
const subtitle = computed(() => {
  if (isLoading.value) return '正在加载昨晚美股盘面...'
  const target = props.summaryData?.target_us_date || '--'
  const rule = props.summaryData?.date_rule || '周一显示上周五美股盘面；其他日期显示前一美股交易日。'
  return `目标美股交易日 ${target} · ${rule}`
})
const metrics = computed(() => (props.summaryData?.metrics || []).slice(0, 8))
const mappingItems = computed(() => (props.summaryData?.sector_mappings || []).slice(0, 5).map(mapping => {
  const mapped = Array.isArray(mapping.a_share_mapping)
    ? mapping.a_share_mapping.slice(0, 4).join(' / ')
    : (mapping.a_share_mapping || '相关板块')
  const sector = mapping.proxy
    ? `${mapping.us_sector || ''}(${mapping.proxy})`
    : (mapping.us_sector || '美股板块')
  const strategy = cleanMarketLine(mapping.strategy || '')
  if (strategy) return `${sector} ${mapping.change_pct_text || '--'} · ${strategy}`
  const bias = cleanMarketLine(mapping.bias || '')
  return `${sector} ${mapping.change_pct_text || '--'} · A股映射：${mapped}${bias ? ` · ${bias}` : ''}`
}))
const guidanceItems = computed(() => {
  const summaryText = cleanMarketLine(summary.value)
  const seen = new Set()
  return (props.summaryData?.guidance_lines || []).slice(0, 7).filter(line => {
    const clean = cleanMarketLine(line)
    if (!clean || summaryText.includes(clean) || seen.has(clean)) return false
    seen.add(clean)
    return true
  })
})
const mappingSection = computed(() => ({
  title: 'A股板块映射', icon: '🧭', tone: 'overview', wide: true, items: mappingItems.value,
}))
const guidanceSection = computed(() => ({
  title: '今日执行', icon: '💡', tone: 'tip', wide: true, items: guidanceItems.value,
}))
function percentageTone(metric) {
  const number = Number(metric?.change_pct)
  if (!Number.isFinite(number) || number === 0) return 'flat'
  return number > 0 ? 'up' : 'down'
}
</script>

<template>
  <section class="us-market-summary-card" :class="[tone, expanded ? 'open' : 'collapsed']">
    <button
      type="button"
      class="us-market-head"
      aria-controls="us-market-summary-body"
      :aria-expanded="expanded"
      :aria-label="`${expanded ? '收起' : '展开'}隔夜美股盘面总结`"
      @click="expanded = !expanded"
    >
      <span>
        <span class="us-market-title">隔夜美股盘面总结</span>
        <span class="us-market-sub">{{ subtitle }}</span>
        <span class="market-card-preview us-market-preview">{{ summary }}</span>
      </span>
      <span class="us-market-head-actions">
        <span class="us-market-tone">{{ toneLabel }}</span>
        <span class="market-chevron us-market-chevron" aria-hidden="true">›</span>
      </span>
    </button>
    <div v-show="expanded" id="us-market-summary-body" class="market-card-detail us-market-summary-body">
      <div class="market-detail-box">
        <div class="market-detail-overview us-market-overview" :class="{ 'no-metrics': !metrics.length }">
          <div class="market-mood-panel">
            <div class="market-mood-label">核心判断</div>
            <div class="market-mood-text">{{ summary }}</div>
          </div>
          <div v-if="metrics.length" class="market-metric-grid">
            <div v-for="metric in metrics" :key="metric.label" class="market-metric-item">
              <div class="market-metric-label">{{ metric.label || '' }}</div>
              <div class="market-metric-value us-market-metric-value">
                <span>{{ metric.value || '--' }}</span>
                <span class="market-num" :class="percentageTone(metric)">{{ metric.change_pct_text || '--' }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="mappingItems.length || guidanceItems.length" class="market-section-list">
          <MarketSection v-if="mappingItems.length" :section="mappingSection" />
          <MarketSection v-if="guidanceItems.length" :section="guidanceSection" />
        </div>
      </div>
    </div>
  </section>
</template>
