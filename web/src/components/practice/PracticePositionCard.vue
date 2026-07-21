<script setup>
import { computed } from 'vue'
import {
  formatPracticeAmount,
  formatPracticeNumber,
  PRACTICE_BUY_NAMES,
  practiceValueColor,
  signedPracticeAmount,
  signedPracticeNumber,
  splitPracticeTags,
  uniquePracticeLabels,
} from '../../utils/practiceDisplay.js'

const props = defineProps({
  position: { type: Object, required: true },
  totalEquity: { type: Number, default: 0 },
  brief: Boolean,
  strategyMeta: { type: Object, default: () => ({}) },
})

const marketValue = computed(() => Number(props.position.market_value))
const positionPct = computed(() => props.totalEquity > 0 && Number.isFinite(marketValue.value)
  ? marketValue.value / props.totalEquity * 100
  : null)
const positionText = computed(() => Number.isFinite(positionPct.value)
  ? `${formatPracticeNumber(positionPct.value)}%`
  : '--')
const pnlValue = computed(() => Number(props.position.pnl))
const pnlPct = computed(() => Number(props.position.pnl_pct))
const pnlText = computed(() => Number.isFinite(pnlValue.value)
  ? `${signedPracticeAmount(pnlValue.value)}${Number.isFinite(pnlPct.value) ? ` / ${signedPracticeNumber(pnlPct.value)}` : ''}`
  : '--')
const todayPnl = computed(() => Number(props.position.today_pnl))
const todayPct = computed(() => Number(props.position.today_pnl_pct ?? props.position.change_pct))
const todayText = computed(() => Number.isFinite(todayPnl.value)
  ? `${signedPracticeAmount(todayPnl.value)}${Number.isFinite(todayPct.value) ? ` / ${signedPracticeNumber(todayPct.value)}` : ''}`
  : '--')
const changePct = computed(() => Number(props.position.change_pct))
const lowPct = computed(() => Number(props.position.day_low_pct))
const highPct = computed(() => Number(props.position.day_high_pct))
const buyStrategyLabels = computed(() => {
  const names = { ...PRACTICE_BUY_NAMES }
  for (const [key, meta] of Object.entries(props.strategyMeta || {})) names[key] = meta?.label || names[key] || key
  return uniquePracticeLabels(splitPracticeTags(props.position.buy_strategy).map(key => names[key] || key))
})
const buyReasonText = computed(() => String(props.position.entry_reason || props.position.buy_reason || '').trim())
</script>

<template>
  <div v-if="brief" class="position-brief-card">
    <div class="position-brief-name">{{ position.name || position.code || '--' }}</div>
    <div class="position-brief-stats">
      <div class="position-brief-item"><span>仓位</span><b>{{ positionText }}</b></div>
      <div class="position-brief-item"><span>盈亏</span><b :style="`color:${practiceValueColor(pnlValue)}`">{{ Number.isFinite(pnlPct) ? signedPracticeNumber(pnlPct) : '--' }}</b></div>
    </div>
  </div>
  <div v-else class="position-card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-weight:700;font-size:16px;color:var(--text)">{{ position.code }} {{ position.name || '' }}</span>
    </div>
    <div class="position-metrics">
      <div class="position-metric"><div class="position-label">成本/现价</div><div class="position-value combo">{{ formatPracticeNumber(position.avg_cost) }} / {{ formatPracticeNumber(position.last_price) }}</div></div>
      <div class="position-metric"><div class="position-label">盈亏</div><div class="position-value strong combo" :style="`color:${practiceValueColor(pnlValue)}`">{{ pnlText }}</div></div>
      <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" :style="`color:${practiceValueColor(changePct)}`">{{ Number.isFinite(changePct) ? signedPracticeNumber(changePct) : '--' }}</div></div>
      <div class="position-metric"><div class="position-label">最低/最高</div><div class="position-value strong combo"><span :style="`color:${practiceValueColor(lowPct)}`">{{ Number.isFinite(lowPct) ? signedPracticeNumber(lowPct) : '--' }}</span><span style="color:#64748b">/</span><span :style="`color:${practiceValueColor(highPct)}`">{{ Number.isFinite(highPct) ? signedPracticeNumber(highPct) : '--' }}</span></div></div>
      <div class="position-metric"><div class="position-label">今日收益</div><div class="position-value strong" :style="`color:${practiceValueColor(todayPnl)}`">{{ todayText }}</div></div>
      <div class="position-metric"><div class="position-label">市值</div><div class="position-value">{{ formatPracticeAmount(position.market_value) }}</div></div>
      <div class="position-metric"><div class="position-label">仓位占比</div><div class="position-value">{{ positionText }}</div></div>
      <div class="position-metric"><div class="position-label">可卖/持有</div><div class="position-value" style="color:#94a3b8">{{ position.available_qty ?? 0 }} / {{ position.qty ?? 0 }}</div></div>
    </div>
    <div v-if="position.bought_today && (buyStrategyLabels.length || buyReasonText)" class="position-reason-block">
      <div v-if="buyStrategyLabels.length" class="position-reason-row">
        <span class="position-reason-label">买入策略</span>
        <span class="position-reason-text"><span class="position-reason-badges"><span v-for="label in buyStrategyLabels" :key="label" class="position-reason-badge">{{ label }}</span></span></span>
      </div>
      <div v-if="buyReasonText" class="position-reason-row"><span class="position-reason-label">买入理由</span><span class="position-reason-text">{{ buyReasonText }}</span></div>
    </div>
  </div>
</template>
