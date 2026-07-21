<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMarketMonitorData } from '../composables/useMarketMonitorData.js'
import {
  groupMarketRecordsByDay,
  isUsMarketSummaryRecord,
  marketRecordKey,
  usMarketSummaryMatchesDay,
} from '../utils/marketMonitorDisplay.js'
import MarketMonitorCard from './market-monitor/MarketMonitorCard.vue'
import UsMarketSummaryCard from './market-monitor/UsMarketSummaryCard.vue'

const { state, activateMarketMonitor, deactivateMarketMonitor } = useMarketMonitorData()
const dayIndex = ref(Math.max(0, Number(new URLSearchParams(location.search).get('day') || 1) - 1))
const expandedKey = ref('')
const groups = computed(() => groupMarketRecordsByDay(state.records))
const days = computed(() => [...groups.value.keys()].sort().reverse())
const selectedDay = computed(() => days.value[dayIndex.value] || days.value[0] || '')
const selectedRecords = computed(() => groups.value.get(selectedDay.value) || [])
const showLiveUsSummary = computed(() => usMarketSummaryMatchesDay(selectedDay.value, state.summary))
const visibleRecords = computed(() => showLiveUsSummary.value
  ? selectedRecords.value.filter(record => !isUsMarketSummaryRecord(record))
  : selectedRecords.value)
const loadedText = computed(() => state.total && state.records.length < state.total
  ? `已载入最近 ${state.records.length} / ${state.total} 条`
  : `共 ${days.value.length} 个日期`)
const atLatest = computed(() => dayIndex.value <= 0)
const atEarliest = computed(() => dayIndex.value >= days.value.length - 1)

function syncDayFromUrl() {
  dayIndex.value = Math.max(0, Number(new URLSearchParams(location.search).get('day') || 1) - 1)
  expandedKey.value = ''
}

function selectDay(index) {
  if (!days.value.length) return
  dayIndex.value = Math.max(0, Math.min(Number(index || 0), days.value.length - 1))
  expandedKey.value = ''
  const next = new URL(location.href)
  if (dayIndex.value > 0) next.searchParams.set('day', String(dayIndex.value + 1))
  else next.searchParams.delete('day')
  history.replaceState(null, '', `${next.pathname}${next.search}${next.hash}`)
}

function toggleCard(key) {
  expandedKey.value = expandedKey.value === key ? '' : key
}

watch(days, nextDays => {
  if (!nextDays.length || dayIndex.value < nextDays.length) return
  selectDay(0)
})

onMounted(() => {
  window.addEventListener('popstate', syncDayFromUrl)
  activateMarketMonitor()
})
onBeforeUnmount(() => {
  window.removeEventListener('popstate', syncDayFromUrl)
  deactivateMarketMonitor()
})
</script>

<template>
  <div v-if="state.loading && !state.loaded" class="loading">加载中…</div>
  <template v-else>
    <div v-if="state.error && state.records.length" class="industry-flow-notice warning">
      自动更新暂时失败，继续展示已缓存盘面：{{ state.error }}
    </div>
    <template v-if="!state.records.length">
      <div class="empty">暂无盘面监控消息</div>
      <UsMarketSummaryCard :summary-data="state.summary" />
    </template>
    <template v-else>
      <div class="market-monitor-grid">
        <MarketMonitorCard
          v-for="record in visibleRecords"
          :key="marketRecordKey(record)"
          :record="record"
          :expanded="expandedKey === marketRecordKey(record)"
          @toggle="toggleCard"
        />
      </div>
      <UsMarketSummaryCard v-if="showLiveUsSummary" :summary-data="state.summary" />
      <div class="sector-cloud market-day-pager">
        <div>
          <div class="market-day-title">{{ selectedDay }} · {{ selectedRecords.length }} 条盘面监控</div>
          <div class="market-day-sub">{{ loadedText }}</div>
        </div>
        <div class="market-day-actions">
          <button class="market-day-btn" type="button" :disabled="atLatest" @click="selectDay(0)">最新</button>
          <button class="market-day-btn" type="button" :disabled="atLatest" @click="selectDay(dayIndex - 1)">后一天</button>
          <button class="market-day-btn" type="button" :disabled="atEarliest" @click="selectDay(dayIndex + 1)">前一天</button>
          <button class="market-day-btn" type="button" :disabled="atEarliest" @click="selectDay(days.length - 1)">最早</button>
        </div>
      </div>
    </template>
  </template>
</template>
