<script setup>
import { computed, onBeforeUnmount, onMounted, toRefs } from 'vue'
import { useRouter } from 'vue-router'
import { useIndicesData } from '../composables/useIndicesData.js'
import { indicesSwitchSession, marketItems } from '../utils/marketDisplay.js'
import IndexOverview from './indices/IndexOverview.vue'
import MarketBreadthChart from './indices/MarketBreadthChart.vue'
import MarketOverview from './indices/MarketOverview.vue'

const INDEX_PRIORITY_STATE_KEY = 'niuniu-dashboard-index-priority-v1'
const router = useRouter()
const { state, view, activateIndices, deactivateIndices } = useIndicesData()

const { panel, marketRegionOverride, indexPriorityOverride } = toRefs(view)

function panelFromLocation() {
  const value = new URLSearchParams(window.location.search).get('panel')
  return ['market', 'market-breadth'].includes(value) ? value : 'index'
}

panel.value = panelFromLocation()
try {
  const saved = window.sessionStorage.getItem(INDEX_PRIORITY_STATE_KEY)
  if (['a_share', 'us'].includes(saved)) indexPriorityOverride.value = saved
} catch {}

const indexItems = computed(() => Array.isArray(state.indices.items) ? state.indices.items : [])
const aIndexItems = computed(() => marketItems(state.indices, 'a_index', 'domestic'))
const session = computed(() => indicesSwitchSession(aIndexItems.value))
const indexPriority = computed(() => indexPriorityOverride.value || (session.value === 'a_share' ? 'a_share' : 'us'))
const marketRegion = computed(() => marketRegionOverride.value || (session.value === 'a_share' ? 'a_share' : 'us'))

const usSectorCount = computed(() => (state.usSectors.items || [])
  .filter(row => row.label || row.name || row.symbol)
  .length)
const aShareModuleCount = computed(() => {
  const sectors = state.sectors
  const hot = state.hotStocks
  const moneyFlow = state.moneyFlow
  const marketFlow = state.marketFlow
  const hasSectorMoves = (sectors.gain_top || sectors.sectors || sectors.items || []).length
    || (sectors.loss_top || []).length
  const hasHotStocks = ['amount_top', 'turnover_top', 'volume_top', 'items']
    .some(key => Array.isArray(hot[key]) && hot[key].length)
  const hasMoneyFlow = moneyFlow.inflow?.length && moneyFlow.outflow?.length
  const hasMarketFlow = marketFlow.total_inflow_yi != null && Boolean(
    Number(marketFlow.total_inflow_yi)
    || Number(marketFlow.total_outflow_yi)
    || Number(marketFlow.net_flow_yi)
  )
  return [hasSectorMoves, hasHotStocks, hasMarketFlow, hasMoneyFlow].filter(Boolean).length
})
const marketModuleCount = computed(() => marketRegion.value === 'us' ? usSectorCount.value : aShareModuleCount.value)
const panelMeta = computed(() => {
  if (panel.value === 'market') {
    return `${marketModuleCount.value} ${marketRegion.value === 'us' ? '项' : '组'}`
  }
  if (panel.value === 'market-breadth') {
    return `${state.marketBreadth.timeline?.length || 0} 个采样点`
  }
  return `${indexItems.value.length} 项`
})

function updateUrl() {
  const nextUrl = new URL(window.location.href)
  if (['market', 'market-breadth'].includes(panel.value)) nextUrl.searchParams.set('panel', panel.value)
  else nextUrl.searchParams.delete('panel')
  window.history.replaceState({}, '', `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`)
}

function selectPanel(nextPanel) {
  if (nextPanel === 'flow') {
    router.push('/industry-flow')
    return
  }
  const normalized = ['market', 'market-breadth'].includes(nextPanel) ? nextPanel : 'index'
  if (panel.value === normalized) return
  panel.value = normalized
  updateUrl()
}

function setIndexPriority(value) {
  if (!['a_share', 'us'].includes(value)) return
  indexPriorityOverride.value = value
  try { window.sessionStorage.setItem(INDEX_PRIORITY_STATE_KEY, value) } catch {}
}

function setMarketRegion(value) {
  if (['a_share', 'us'].includes(value)) marketRegionOverride.value = value
}

function syncPanelFromLocation() {
  panel.value = panelFromLocation()
}

onMounted(() => {
  activateIndices()
  window.addEventListener('popstate', syncPanelFromLocation)
})
onBeforeUnmount(() => {
  window.removeEventListener('popstate', syncPanelFromLocation)
  deactivateIndices()
})
</script>

<template>
  <div v-if="state.indices.error" class="empty" style="color:#f87171;margin-bottom:12px">
    指数接口错误：{{ state.indices.error }}
  </div>
  <div v-if="state.indices.stale_cache" class="indices-cache-notice" role="status">
    <span>正在后台更新实时行情</span>
    <span>当前展示 {{ state.indices.generated_at || '上次成功' }} 缓存</span>
  </div>
  <div v-if="state.loading && !indexItems.length && !state.indices.error" class="loading">行情加载中...</div>
  <div v-else class="indices-page">
    <div class="indices-switch" role="group" aria-label="指数行情、资金流动与市场情绪切换">
      <button type="button" class="indices-switch-btn" :class="{ active: panel === 'index' }" :aria-pressed="panel === 'index'" @click="selectPanel('index')">指数</button>
      <button type="button" class="indices-switch-btn" :class="{ active: panel === 'market' }" :aria-pressed="panel === 'market'" @click="selectPanel('market')">行情</button>
      <button type="button" class="indices-switch-btn" aria-pressed="false" @click="selectPanel('flow')">资金流动</button>
      <button type="button" class="indices-switch-btn" :class="{ active: panel === 'market-breadth' }" :aria-pressed="panel === 'market-breadth'" @click="selectPanel('market-breadth')">市场情绪</button>
    </div>
    <section class="indices-part" :id="panel === 'market' ? 'market-overview' : panel === 'market-breadth' ? 'market-breadth-overview' : 'indices-overview'">
      <div class="indices-part-head">
        <div class="indices-part-title-row">
          <h2 v-if="panel === 'index'" class="indices-part-title">指数</h2>
          <h2 v-else-if="panel === 'market-breadth'" class="indices-part-title">市场情绪</h2>
          <div
            v-if="panel === 'index'"
            class="market-region-switch index-priority-switch"
            role="group"
            aria-label="指数排序切换"
            :title="indexPriorityOverride ? '当前为手动排序' : '当前按交易时段自动排序'"
          >
            <button type="button" class="market-region-btn" :class="{ active: indexPriority === 'a_share' }" :aria-pressed="indexPriority === 'a_share'" @click="setIndexPriority('a_share')">A股在上</button>
            <button type="button" class="market-region-btn" :class="{ active: indexPriority === 'us' }" :aria-pressed="indexPriority === 'us'" @click="setIndexPriority('us')">美股在上</button>
          </div>
          <div
            v-else-if="panel === 'market'"
            class="market-region-switch"
            role="group"
            aria-label="行情市场切换"
            :title="marketRegionOverride ? '当前为手动选择' : '当前按交易时段自动选择'"
          >
            <button type="button" class="market-region-btn" :class="{ active: marketRegion === 'a_share' }" :aria-pressed="marketRegion === 'a_share'" @click="setMarketRegion('a_share')">A股</button>
            <button type="button" class="market-region-btn" :class="{ active: marketRegion === 'us' }" :aria-pressed="marketRegion === 'us'" @click="setMarketRegion('us')">美股</button>
          </div>
        </div>
        <div class="indices-part-meta">{{ panelMeta }}</div>
      </div>
      <div :class="panel === 'market' ? 'indices-market-stack' : 'indices-index-stack'">
        <IndexOverview
          v-if="panel === 'index'"
          :payload="state.indices"
          :priority="indexPriority"
        />
        <MarketOverview
          v-else-if="panel === 'market'"
          :sectors="state.sectors"
          :us-sectors="state.usSectors"
          :hot-stocks="state.hotStocks"
          :money-flow="state.moneyFlow"
          :market-flow="state.marketFlow"
          :region="marketRegion"
        />
        <MarketBreadthChart v-else :payload="state.marketBreadth" />
      </div>
    </section>
  </div>
</template>
