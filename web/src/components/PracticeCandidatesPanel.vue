<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { usePracticeCandidatesData } from '../composables/usePracticeCandidatesData.js'
import {
  practiceCandidateStrategyMeta,
  practiceCandidateTierCounts,
} from '../utils/practiceCandidateDisplay.js'
import PracticeCandidateCard from './practice/PracticeCandidateCard.vue'

const { state, activatePracticeCandidates, deactivatePracticeCandidates } = usePracticeCandidatesData()
const strategyMeta = computed(() => practiceCandidateStrategyMeta(state.strategyMeta))
const tierCounts = computed(() => practiceCandidateTierCounts(state.items))
const statusText = computed(() => state.running
  ? `计算中${state.startedAt ? ` · 开始 ${state.startedAt.slice(11)}` : ''}`
  : `扫描时间：${state.generatedAt || '--'} · 高流动性主板扫描 ${state.count || state.items.length} 只入选`)
const distribution = computed(() => Object.entries(state.strategyDistribution || {})
  .filter(([, count]) => Number(count) > 0)
  .map(([name, count]) => ({
    name,
    count: Number(count) || 0,
    ...(strategyMeta.value[name] || { label: name, color: '#94a3b8' }),
  })))

onMounted(activatePracticeCandidates)
onBeforeUnmount(deactivatePracticeCandidates)
</script>

<template>
  <section aria-label="模拟交易候选股">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;color:var(--muted);font-size:13px;flex-wrap:wrap">
      <span>{{ statusText }}</span>
    </div>
    <div v-if="state.running" class="empty" style="border-color:var(--accent-border);color:var(--accent-text);background:var(--accent-soft)">
      多战法正在计算中，完成后页面会自动刷新；当前下方仍显示上一版缓存结果。
    </div>
    <div v-if="state.loading && !state.loaded" class="loading">候选股加载中...</div>
    <div v-else-if="state.error && !state.items.length" class="empty" style="color:#f87171">⚠️ {{ state.error }}</div>
    <template v-else-if="state.items.length">
      <div v-if="state.error" class="industry-flow-notice warning">候选股自动更新暂时失败，继续展示缓存结果：{{ state.error }}</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px">
        <span style="padding:4px 9px;border-radius:6px;background:var(--green-soft);color:var(--green-text);border:1px solid var(--green-border);font-size:12px">试仓 {{ tierCounts.high }}只</span>
        <span style="padding:4px 9px;border-radius:6px;background:var(--yellow-soft);color:var(--yellow-text);border:1px solid var(--yellow-border);font-size:12px">等确认 {{ tierCounts.mid }}只</span>
        <span style="padding:4px 9px;border-radius:6px;background:var(--panel2);color:var(--muted);border:1px solid var(--line);font-size:12px">仅观察 {{ tierCounts.low }}只</span>
      </div>
      <div v-if="distribution.length" style="display:flex;flex-wrap:wrap;gap:8px;margin:-8px 0 18px">
        <span
          v-for="entry in distribution"
          :key="entry.name"
          :style="`padding:4px 10px;border-radius:999px;background:${entry.color}18;color:${entry.color};border:1px solid ${entry.color}38;font-size:12px`"
        >{{ entry.label }} {{ entry.count }}</span>
      </div>
      <div style="display:grid;gap:12px">
        <PracticeCandidateCard
          v-for="item in state.items"
          :key="`${item.code || item.name}-${item.best_strategy || item.score || ''}`"
          :item="item"
          :strategy-meta="strategyMeta"
        />
      </div>
    </template>
    <div v-else class="empty">暂无多战法结果，请等待扫描完成…</div>
  </section>
</template>
