<script setup>
import { computed } from 'vue'
import { formatPracticeAmount, formatPracticeNumber } from '../../utils/practiceDisplay.js'
import PracticeMarketSummary from './PracticeMarketSummary.vue'
import PracticePositions from './PracticePositions.vue'

const props = defineProps({
  practice: { type: Object, required: true },
  manualCycle: { type: Object, required: true },
  marketSummary: { type: Object, required: true },
  marketSummaryGenerating: Boolean,
  strategyMeta: { type: Object, default: () => ({}) },
  error: { type: String, default: '' },
})
const emit = defineEmits(['manual-cycle', 'market-summary', 'resume'])

const pnl = computed(() => Number(props.practice.total_pnl || 0))
const marketContext = computed(() => props.practice.market_decision_context || {})
const marketGuidance = computed(() => Array.isArray(marketContext.value.guidance_lines)
  ? marketContext.value.guidance_lines.slice(0, 2)
  : [])
const manualRunning = computed(() => props.manualCycle.running === true)
const manualButtonText = computed(() => manualRunning.value
  ? (props.manualCycle.stage_label || '本轮执行中…')
  : '手动运行选股与交易策略')
</script>

<template>
  <section class="sector-cloud" style="margin-bottom:18px">
    <div class="practice-account-head">
      <h3>模拟账户</h3>
      <button
        type="button"
        class="practice-manual-cycle-btn"
        :disabled="manualRunning"
        :aria-busy="manualRunning ? 'true' : undefined"
        @click="emit('manual-cycle')"
      >{{ manualRunning ? '处理中 · ' : '' }}{{ manualButtonText }}</button>
    </div>
    <div v-if="marketContext.available || marketContext.tone_label" class="practice-market-evaluation">
      <span class="practice-market-evaluation-label">盘面评价 · {{ marketContext.tone_label || '中性' }}</span>
      <span>{{ marketGuidance.join('；') || marketContext.source_title || '已更新' }}</span>
      <time>{{ String(marketContext.source_time || marketContext.context_as_of || '').slice(5, 16) }}</time>
    </div>
    <PracticeMarketSummary
      :summary="marketSummary"
      :generating="marketSummaryGenerating"
      @generate="emit('market-summary')"
    />
    <div v-if="manualCycle.error" class="practice-manual-cycle-error">本轮执行失败：{{ manualCycle.error }}</div>
    <div v-if="practice.trading_paused" style="background:var(--yellow-soft);border:1px solid var(--yellow-border);border-radius:8px;padding:10px 14px;margin:10px 0;display:flex;justify-content:space-between;align-items:center">
      <span style="color:var(--yellow-text);font-size:13px">新开仓已暂停：{{ practice.pause_reason || '风控触发' }}（{{ String(practice.pause_since || '').slice(11, 16) }}起，卖出风控继续运行）</span>
      <button type="button" style="background:var(--green-soft);color:var(--green-text);border:1px solid var(--green-border);border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12px;font-weight:600" @click="emit('resume')">恢复交易</button>
    </div>
    <div class="practice-stats" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0">
      <div class="inline-field"><div class="inline-label">初始资金</div><div class="inline-value">{{ formatPracticeAmount(practice.initial_cash) }}</div></div>
      <div class="inline-field"><div class="inline-label">总权益</div><div class="inline-value">{{ formatPracticeAmount(practice.total_equity) }}</div></div>
      <div class="inline-field"><div class="inline-label">现金</div><div class="inline-value">{{ formatPracticeAmount(practice.cash) }}</div></div>
      <div class="inline-field"><div class="inline-label">累计收益</div><div class="inline-value" :class="pnl >= 0 ? 'up' : 'down'">{{ formatPracticeAmount(practice.total_pnl) }} / {{ formatPracticeNumber(practice.total_pnl_pct) }}%</div></div>
    </div>
    <slot name="chart" />
    <PracticePositions
      :positions="practice.positions || []"
      :sold-stocks="practice.today_sold_stocks || []"
      :total-equity="Number(practice.total_equity || 0)"
      :strategy-meta="strategyMeta"
    />
    <slot name="activity" />
    <slot name="rule" />
    <div v-if="practice.last_error" class="empty" style="color:#f87171;margin-top:10px">模型/交易错误：{{ practice.last_error }}</div>
    <div v-if="error && !practice.last_error" class="empty" style="color:#f87171;margin-top:10px">模拟账户更新错误：{{ error }}</div>
  </section>
</template>
