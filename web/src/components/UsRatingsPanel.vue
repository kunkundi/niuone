<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useUsRatingsData } from '../composables/useUsRatingsData.js'
import {
  groupRatingRecordsByDay,
  ratingRecordKey,
  shortRatingDate,
} from '../utils/usRatingDisplay.js'
import UsRatingCard from './us-ratings/UsRatingCard.vue'

const {
  state,
  activateUsRatings,
  deactivateUsRatings,
  loadQuotesForRecords,
  loadProfile,
} = useUsRatingsData()
const dayIndex = ref(0)
const groups = computed(() => groupRatingRecordsByDay(state.records))
const days = computed(() => [...groups.value.keys()].sort().reverse())
const selectedDay = computed(() => days.value[dayIndex.value] || days.value[0] || '')
const selectedRecords = computed(() => groups.value.get(selectedDay.value) || [])
const olderDay = computed(() => days.value[dayIndex.value + 1] || '')
const newerDay = computed(() => days.value[dayIndex.value - 1] || '')

function selectDay(index) {
  if (!days.value.length) return
  dayIndex.value = Math.max(0, Math.min(Number(index || 0), days.value.length - 1))
}

watch(days, nextDays => {
  if (!nextDays.length || dayIndex.value < nextDays.length) return
  dayIndex.value = 0
})
watch(selectedRecords, records => loadQuotesForRecords(records), { immediate: true })

onMounted(() => activateUsRatings())
onBeforeUnmount(() => deactivateUsRatings())
</script>

<template>
  <div v-if="state.loading && !state.loaded" class="loading">加载中…</div>
  <template v-else-if="!days.length">
    <div class="empty">暂无美股机构买入评级消息</div>
  </template>
  <template v-else>
    <div v-if="state.error && state.records.length" class="industry-flow-notice warning">
      自动更新暂时失败，继续展示已缓存评级：{{ state.error }}
    </div>
    <div class="sector-cloud" style="margin-bottom:14px">
      <div class="rating-day-pager" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-weight:700;color:var(--text)">{{ selectedDay }}</span>
        <div class="rating-day-actions" style="display:flex;gap:8px">
          <button
            type="button"
            title="查看更早的评级日报"
            :disabled="!olderDay"
            style="padding:5px 10px;font-size:12px"
            @click="selectDay(dayIndex + 1)"
          >‹ {{ olderDay ? `更早 ${shortRatingDate(olderDay)}` : '已是最早' }}</button>
          <button
            type="button"
            title="回到更新的评级日报"
            :disabled="!newerDay"
            style="padding:5px 10px;font-size:12px"
            @click="selectDay(dayIndex - 1)"
          >{{ newerDay ? `更新 ${shortRatingDate(newerDay)}` : '已是最新' }} ›</button>
        </div>
      </div>
    </div>
    <div style="display:grid;gap:14px">
      <UsRatingCard
        v-for="record in selectedRecords"
        :key="ratingRecordKey(record)"
        :record="record"
        :quotes="state.quotes"
        :profiles="state.profiles"
        :load-profile="loadProfile"
      />
    </div>
  </template>
</template>
