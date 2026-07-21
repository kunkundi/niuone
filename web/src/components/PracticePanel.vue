<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { usePracticeCandidatesData } from '../composables/usePracticeCandidatesData.js'
import { usePracticeData } from '../composables/usePracticeData.js'
import PracticeCandidatesPanel from './PracticeCandidatesPanel.vue'
import PracticeAccountOverview from './practice/PracticeAccountOverview.vue'
import PracticeCalendar from './practice/PracticeCalendar.vue'
import PracticeEquityChart from './practice/PracticeEquityChart.vue'
import PracticeOperationLog from './practice/PracticeOperationLog.vue'
import PracticeRule from './practice/PracticeRule.vue'

const calendarOpen = ref(false)
const { state: candidateState } = usePracticeCandidatesData()
const {
  state,
  activatePractice,
  deactivatePractice,
  ensureFullSnapshot,
  resumeTrading,
  triggerManualCycle,
  triggerMarketSummary,
} = usePracticeData()

const strategyMeta = computed(() => candidateState.strategyMeta || {})

onMounted(activatePractice)
onBeforeUnmount(deactivatePractice)
</script>

<template>
  <div v-if="state.loading && !state.loaded" class="loading">模拟账户加载中...</div>
  <div v-else-if="state.error && !state.loaded" class="empty" style="color:#f87171">⚠️ {{ state.error }}</div>
  <template v-else>
    <PracticeAccountOverview
      :practice="state.practice"
      :manual-cycle="state.manualCycle"
      :market-summary="state.marketSummary"
      :market-summary-generating="state.marketSummaryGenerating"
      :strategy-meta="strategyMeta"
      :error="state.error"
      @manual-cycle="triggerManualCycle"
      @market-summary="triggerMarketSummary"
      @resume="resumeTrading"
    >
      <template #chart>
        <PracticeEquityChart
          :practice="state.practice"
          @open-calendar="calendarOpen = true"
        />
      </template>
      <template #activity>
        <PracticeOperationLog :practice="state.practice" />
      </template>
      <template #rule>
        <PracticeRule
          :practice="state.practice"
          :full-snapshot-status="state.fullSnapshotStatus"
        />
      </template>
    </PracticeAccountOverview>
    <PracticeCandidatesPanel />
    <PracticeCalendar
      :open="calendarOpen"
      :practice="state.practice"
      :full-snapshot-status="state.fullSnapshotStatus"
      @close="calendarOpen = false"
      @ensure-full="ensureFullSnapshot"
    />
  </template>
</template>
