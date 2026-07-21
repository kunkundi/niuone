<script setup>
import { computed } from 'vue'
import { marketRecordKey, summarizeMarketRecord } from '../../utils/marketMonitorDisplay.js'
import MarketDetail from './MarketDetail.vue'

const props = defineProps({
  record: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle'])

const key = computed(() => marketRecordKey(props.record))
const summary = computed(() => summarizeMarketRecord(props.record))

function chipTone(text) {
  if (/\s-\d/.test(text)) return 'down'
  if (/\s\+\d/.test(text)) return 'up'
  return ''
}

function toggle(event) {
  if (event?.target?.closest?.('.market-card-detail')) return
  emit('toggle', key.value)
}

function handleKey(event) {
  if (!['Enter', ' '].includes(event.key)) return
  event.preventDefault()
  emit('toggle', key.value)
}
</script>

<template>
  <article
    class="market-monitor-card"
    :class="{ open: expanded }"
    :data-market-key="key"
    :aria-expanded="expanded"
    role="button"
    tabindex="0"
    @click="toggle"
    @keydown="handleKey"
  >
    <div class="market-card-head">
      <div>
        <div class="market-card-title-row">
          <span class="market-card-title">{{ summary.title }}</span>
          <span v-if="summary.time" class="market-card-time">{{ summary.time }}</span>
        </div>
        <div class="market-card-preview">{{ summary.preview || '等待盘面摘要' }}</div>
        <div v-if="summary.chips.length" class="market-chip-row">
          <span v-for="chip in summary.chips" :key="chip" class="market-chip" :class="chipTone(chip)">{{ chip }}</span>
        </div>
      </div>
      <div class="market-card-side">
        <span class="market-type">{{ summary.type }}</span>
        <span class="market-chevron">›</span>
      </div>
    </div>
    <div v-if="expanded" class="market-card-detail">
      <MarketDetail :content="record.content || ''" />
    </div>
  </article>
</template>
