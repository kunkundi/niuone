<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PracticePositionCard from './PracticePositionCard.vue'
import PracticeSoldCard from './PracticeSoldCard.vue'

const props = defineProps({
  positions: { type: Array, default: () => [] },
  soldStocks: { type: Array, default: () => [] },
  totalEquity: { type: Number, default: 0 },
  strategyMeta: { type: Object, default: () => ({}) },
})

const params = new URLSearchParams(location.search)
const mode = ref(params.get('holdings') === 'sold' ? 'sold' : 'open')
const brief = ref(params.get('brief') === '1')
const showSold = computed(() => mode.value === 'sold')

function syncUrl() {
  const next = new URL(location.href)
  if (mode.value === 'sold') next.searchParams.set('holdings', 'sold')
  else next.searchParams.delete('holdings')
  if (brief.value && mode.value === 'open') next.searchParams.set('brief', '1')
  else next.searchParams.delete('brief')
  history.replaceState(null, '', `${next.pathname}${next.search}${next.hash}`)
}

function setMode(nextMode) {
  mode.value = nextMode === 'sold' ? 'sold' : 'open'
  syncUrl()
}

function setBrief(enabled) {
  brief.value = Boolean(enabled)
  syncUrl()
}

function restoreFromUrl() {
  const nextParams = new URLSearchParams(location.search)
  mode.value = nextParams.get('holdings') === 'sold' ? 'sold' : 'open'
  brief.value = nextParams.get('brief') === '1'
}

onMounted(() => window.addEventListener('popstate', restoreFromUrl))
onBeforeUnmount(() => window.removeEventListener('popstate', restoreFromUrl))
</script>

<template>
  <div style="display:flex;align-items:center;justify-content:flex-start;gap:12px;flex-wrap:wrap;margin:12px 0 8px">
    <div class="practice-mode-control" aria-label="持仓视图">
      <button class="practice-mode-btn" :class="{ active: !showSold }" type="button" @click="setMode('open')">当前持仓{{ positions.length ? ` ${positions.length}` : '' }}</button>
      <button class="practice-mode-btn" :class="{ active: showSold }" type="button" @click="setMode('sold')">今日卖出{{ soldStocks.length ? ` ${soldStocks.length}` : '' }}</button>
    </div>
    <div v-if="!showSold" class="practice-mode-control" aria-label="持仓显示模式">
      <button class="practice-mode-btn" :class="{ active: !brief }" type="button" @click="setBrief(false)">完整</button>
      <button class="practice-mode-btn" :class="{ active: brief }" type="button" @click="setBrief(true)">简要</button>
    </div>
  </div>
  <div v-if="showSold" class="position-card-list">
    <PracticeSoldCard v-for="sold in soldStocks" :key="`${sold.code}-${sold.last_sell_time || ''}`" :sold="sold" />
    <div v-if="!soldStocks.length" class="empty" style="padding:18px;font-size:13px">今日暂无卖出股票</div>
  </div>
  <div v-else :class="positions.length && brief ? 'position-brief-grid' : 'position-card-list'">
    <PracticePositionCard
      v-for="position in positions"
      :key="position.code"
      :position="position"
      :total-equity="totalEquity"
      :brief="brief"
      :strategy-meta="strategyMeta"
    />
    <div v-if="!positions.length" class="empty" style="padding:18px;font-size:13px">暂无持仓，等待模型决策建仓</div>
  </div>
</template>
