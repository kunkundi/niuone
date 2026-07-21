<script setup>
import { computed } from 'vue'
import {
  marketDetailLine,
  marketMoodLine,
  marketSectionDisplayItems,
  marketSummaryMetrics,
  parseMarketDetail,
} from '../../utils/marketMonitorDisplay.js'
import MarketDetailLine from './MarketDetailLine.vue'
import MarketSection from './MarketSection.vue'

const props = defineProps({
  content: { type: String, default: '' },
})

const parsed = computed(() => parseMarketDetail(props.content))
const mood = computed(() => marketMoodLine(parsed.value.sections))
const metrics = computed(() => marketSummaryMetrics(parsed.value.sections))
const introLines = computed(() => parsed.value.intro
  .filter(line => !/^牛牛大王[，,]/.test(line))
  .map(line => marketDetailLine(line))
  .filter(Boolean))
const sections = computed(() => parsed.value.sections.filter(section => {
  if (marketSectionDisplayItems(section).length) return true
  return Boolean(section.meta && !/市场概况|竞价情绪/.test(section.title || ''))
}))
const fallbackLines = computed(() => String(props.content || '')
  .split('\n')
  .map(line => marketDetailLine(line))
  .filter(Boolean))
const hasStructuredDetail = computed(() => Boolean(
  mood.value || metrics.value.length || introLines.value.length || sections.value.length,
))
</script>

<template>
  <div class="market-detail-box">
    <div v-if="mood || metrics.length" class="market-detail-overview">
      <div v-if="mood" class="market-mood-panel">
        <div class="market-mood-label">核心判断</div>
        <div class="market-mood-text">{{ mood }}</div>
      </div>
      <div v-if="metrics.length" class="market-metric-grid">
        <div v-for="metric in metrics" :key="metric.label" class="market-metric-item">
          <div class="market-metric-label">{{ metric.label }}</div>
          <div class="market-metric-value" :class="metric.tone">{{ metric.value }}</div>
        </div>
      </div>
    </div>
    <div v-if="introLines.length" class="market-section-list">
      <section class="market-section wide">
        <div class="market-section-head">
          <div class="market-section-title-wrap">
            <span class="market-section-icon">•</span>
            <span class="market-section-title">摘要</span>
          </div>
        </div>
        <div class="market-section-body">
          <MarketDetailLine v-for="(line, index) in introLines" :key="index" :line="line" />
        </div>
      </section>
    </div>
    <div v-if="sections.length" class="market-section-list">
      <MarketSection v-for="(section, index) in sections" :key="`${section.title}-${index}`" :section="section" />
    </div>
    <template v-if="!hasStructuredDetail">
      <MarketDetailLine v-for="(line, index) in fallbackLines" :key="index" :line="line" />
    </template>
  </div>
</template>
