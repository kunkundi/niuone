<script setup>
import { computed } from 'vue'
import {
  formatPracticeNumber,
  PRACTICE_TIDE_STATUS_LABELS,
  practiceCandidateIndustryLabel,
  practiceCandidateTier,
} from '../../utils/practiceCandidateDisplay.js'

const props = defineProps({
  item: { type: Object, required: true },
  strategyMeta: { type: Object, required: true },
})

const strategyName = computed(() => String(props.item.best_strategy || ''))
const strategy = computed(() => props.strategyMeta[strategyName.value] || {
  label: strategyName.value || '综合',
  color: '#94a3b8',
})
const tideStrategy = computed(() => ['tide_leader', 'tide_rotation', 'tide_recovery'].includes(strategyName.value))
const hardBlockers = computed(() => Array.isArray(props.item.hard_blockers) ? props.item.hard_blockers : [])
const riskFlags = computed(() => Array.isArray(props.item.risk_flags) ? props.item.risk_flags : [])
const tier = computed(() => practiceCandidateTier(props.item))
const tierLabel = computed(() => ({ high: '交易达标', mid: hardBlockers.value.length ? '硬过滤' : '等确认', low: '仅观察' })[tier.value])
const tierStyle = computed(() => {
  const common = 'display:inline-flex;align-items:center;flex:0 0 auto;white-space:nowrap;line-height:1;padding:6px 9px;border-radius:6px;font-size:11px;font-weight:600'
  if (tier.value === 'high') return `${common};background:var(--green-soft);color:var(--green-text);border:1px solid var(--green-border)`
  if (tier.value === 'mid') return `${common};background:var(--yellow-soft);color:var(--yellow-text);border:1px solid var(--yellow-border)`
  return `${common};background:var(--panel2);color:var(--muted);border:1px solid var(--line)`
})
const industryLabel = computed(() => practiceCandidateIndustryLabel(props.item))
const change = computed(() => Number(props.item.change_pct))
const changeText = computed(() => Number.isFinite(change.value)
  ? `${change.value > 0 ? '+' : ''}${change.value.toFixed(2)}%`
  : '--')
const changeClass = computed(() => change.value > 0 ? 'up' : change.value < 0 ? 'down' : 'flat')
const distance = computed(() => Number(props.item.distance_pct))
const distanceText = computed(() => Number.isFinite(distance.value)
  ? `${distance.value > 0 ? '+' : ''}${distance.value.toFixed(2)}%`
  : '--')
const score = computed(() => props.item.best_score ?? props.item.score ?? 0)
const threshold = computed(() => Number(props.item.entry_threshold ?? 8))
const scoreBasis = computed(() => String(props.item.score_basis || ''))
const tradeDiscipline = computed(() => [props.item.position_hint, props.item.time_stop].filter(Boolean).join(' · '))
</script>

<template>
  <article style="background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px">
      <div style="min-width:0;flex:1 1 auto">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0">
          <span style="font-weight:780;font-size:17px;color:var(--text)">{{ item.code }} {{ item.name }}</span>
          <span
            :style="`display:inline-flex;align-items:center;white-space:nowrap;padding:2px 8px;border-radius:999px;background:${strategy.color}22;color:${strategy.color};font-size:12px;border:1px solid ${strategy.color}44`"
          >{{ strategy.label }}</span>
        </div>
        <div v-if="industryLabel" style="margin-top:8px">
          <span style="display:inline-flex;align-items:center;max-width:100%;white-space:nowrap;padding:2px 8px;border-radius:6px;background:var(--accent-soft);color:var(--accent-text);font-size:12px">{{ industryLabel }}</span>
        </div>
      </div>
      <span :style="tierStyle">{{ tierLabel }}</span>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 10px;flex:1;min-width:100px">
        <div style="color:var(--muted);font-size:11px">价格 / 涨跌</div>
        <div style="color:var(--text);font-size:14px;font-weight:600">
          {{ formatPracticeNumber(item.price) }} <span class="index-change" :class="changeClass" style="font-size:13px">{{ changeText }}</span>
        </div>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 10px;flex:1;min-width:100px">
        <div style="color:var(--muted);font-size:11px">{{ strategy.label }}评分</div>
        <div style="color:var(--text);font-size:14px;font-weight:600">{{ score }}/{{ item.score_total || 10 }} · 基准≥{{ threshold }}</div>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 10px;flex:1;min-width:100px">
        <div style="color:var(--muted);font-size:11px">{{ tideStrategy ? 'EMA20 / 距EMA20' : 'BBI / 距BBI' }}</div>
        <div style="color:var(--text);font-size:14px;font-weight:600">{{ formatPracticeNumber(tideStrategy ? item.ema20 : item.bbi) }} / {{ distanceText }}</div>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 10px;flex:1;min-width:100px">
        <div style="color:var(--muted);font-size:11px">成交额</div>
        <div style="color:var(--text);font-size:14px;font-weight:600">{{ item.amount_yi != null ? `${item.amount_yi}亿` : '--' }}</div>
      </div>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;color:var(--muted);font-size:12px">
      <template v-if="tideStrategy">
        <span>市场 {{ item.market_regime || '--' }} {{ formatPracticeNumber(item.market_score) }}</span>
        <span>行业潮位 {{ PRACTICE_TIDE_STATUS_LABELS[item.sector_status] || item.sector_status || '--' }} / {{ formatPracticeNumber(item.sector_score) }}</span>
        <span>板块内排名 {{ formatPracticeNumber(item.stock_sector_rank) }}</span>
        <span>结构止损 {{ formatPracticeNumber(item.stop_price) }} ({{ formatPracticeNumber(item.stop_distance_pct) }}%)</span>
        <span>跳空缓冲 {{ formatPracticeNumber(item.gap_buffer_pct) }}%</span>
        <span>有效损失 {{ formatPracticeNumber(item.effective_loss_distance_pct) }}%</span>
        <span>单笔预算 {{ formatPracticeNumber(item.per_trade_risk_budget_pct) }}%</span>
        <span>动态仓位上限 {{ formatPracticeNumber(item.max_position_pct_by_risk) }}%</span>
      </template>
      <template v-else>
        <span>BBI上行 {{ item.bbi_upward ? '✅' : '❌' }}</span>
        <span>站上BBI {{ item.above_bbi ? '✅' : '❌' }}</span>
        <span v-if="item.min_j_10d != null">J最低 {{ Number(item.min_j_10d).toFixed(1) }} {{ item.j_recovering ? '📈回升' : item.j_oversold ? '📉续降' : '--' }}</span>
      </template>
      <span v-if="scoreBasis">{{ scoreBasis }}</span>
      <span v-if="tradeDiscipline">{{ tradeDiscipline }}</span>
      <span v-for="flag in hardBlockers" :key="`hard-${flag}`" style="color:#fbbf24;font-size:11px;margin-left:6px">硬过滤:{{ flag }}</span>
      <span v-for="flag in riskFlags" :key="`risk-${flag}`" style="color:#f87171;font-size:11px;margin-left:6px">⚠️{{ flag }}</span>
    </div>
  </article>
</template>
