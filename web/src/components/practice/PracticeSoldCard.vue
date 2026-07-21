<script setup>
import { computed } from 'vue'
import {
  formatPracticeAmount,
  formatPracticeNumber,
  inferPracticeExitRules,
  PRACTICE_EXIT_NAMES,
  practiceValueColor,
  signedPracticeAmount,
  signedPracticeNumber,
  splitPracticeTags,
  uniquePracticeLabels,
} from '../../utils/practiceDisplay.js'

const props = defineProps({ sold: { type: Object, required: true } })
const realized = computed(() => Number(props.sold.realized_pnl))
const realizedPct = computed(() => Number(props.sold.realized_pnl_pct))
const afterPnl = computed(() => Number(props.sold.after_sell_pnl))
const afterPct = computed(() => Number(props.sold.change_after_sell_pct))
const currentPct = computed(() => Number(props.sold.current_change_pct))
const realizedText = computed(() => Number.isFinite(realized.value)
  ? `${signedPracticeAmount(realized.value)}${Number.isFinite(realizedPct.value) ? ` / ${signedPracticeNumber(realizedPct.value)}` : ''}`
  : '--')
const afterText = computed(() => Number.isFinite(afterPnl.value)
  ? `${signedPracticeAmount(afterPnl.value)}${Number.isFinite(afterPct.value) ? ` / ${signedPracticeNumber(afterPct.value)}` : ''}`
  : '--')
const observation = computed(() => Number.isFinite(afterPnl.value)
  ? (afterPnl.value > 0 ? '卖出后上涨' : afterPnl.value < 0 ? '卖出后回落' : '卖出后持平')
  : '等待行情')
const reasonText = computed(() => String(props.sold.reason || '').trim())
const exitRuleLabels = computed(() => {
  const rawRules = Array.isArray(props.sold.exit_rules) && props.sold.exit_rules.length
    ? props.sold.exit_rules
    : props.sold.exit_rule
  const ruleKeys = splitPracticeTags(rawRules)
  return uniquePracticeLabels(
    (ruleKeys.length ? ruleKeys : inferPracticeExitRules(reasonText.value))
      .map(key => PRACTICE_EXIT_NAMES[key] || key),
  )
})
const afterColor = computed(() => Number.isFinite(afterPnl.value)
  ? (afterPnl.value > 0 ? '#f59e0b' : afterPnl.value < 0 ? '#34d399' : '#94a3b8')
  : '#94a3b8')
</script>

<template>
  <div class="position-card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px">
      <span style="font-weight:700;font-size:16px;color:var(--text)">{{ sold.code }} {{ sold.name || '' }}</span>
      <span style="font-size:13px;color:var(--muted)">{{ sold.shares }}股 · {{ String(sold.last_sell_time || '').slice(11, 16) }}</span>
    </div>
    <div class="position-metrics">
      <div class="position-metric"><div class="position-label">卖出/现价</div><div class="position-value combo">{{ formatPracticeNumber(sold.avg_sell_price) }} / {{ sold.current_price == null ? '--' : formatPracticeNumber(sold.current_price) }}</div></div>
      <div class="position-metric"><div class="position-label">已实现盈亏</div><div class="position-value strong combo" :style="`color:${practiceValueColor(realized)}`">{{ realizedText }}</div></div>
      <div class="position-metric"><div class="position-label">卖后变化</div><div class="position-value strong combo" :style="`color:${afterColor}`">{{ afterText }}</div></div>
      <div class="position-metric"><div class="position-label">观察</div><div class="position-value strong" :style="`color:${afterColor}`">{{ observation }}</div></div>
      <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" :style="`color:${practiceValueColor(currentPct)}`">{{ Number.isFinite(currentPct) ? signedPracticeNumber(currentPct) : '--' }}</div></div>
      <div class="position-metric"><div class="position-label">卖出金额</div><div class="position-value">{{ formatPracticeAmount(sold.sell_amount) }}</div></div>
      <div class="position-metric"><div class="position-label">到账金额</div><div class="position-value">{{ formatPracticeAmount(sold.net_proceeds) }}</div></div>
      <div class="position-metric"><div class="position-label">费用</div><div class="position-value" style="color:#94a3b8">{{ formatPracticeAmount(sold.fee) }}</div></div>
    </div>
    <div v-if="exitRuleLabels.length || reasonText" class="position-reason-block">
      <div v-if="exitRuleLabels.length" class="position-reason-row"><span class="position-reason-label">卖出归因</span><span class="position-reason-text"><span class="position-reason-badges"><span v-for="label in exitRuleLabels" :key="label" class="position-reason-badge">{{ label }}</span></span></span></div>
      <div v-if="reasonText" class="position-reason-row"><span class="position-reason-label">卖出理由</span><span class="position-reason-text">{{ reasonText }}</span></div>
    </div>
  </div>
</template>
