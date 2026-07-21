<script setup>
import { computed } from 'vue'
import { marketDetailLine, marketSectionDisplayItems } from '../../utils/marketMonitorDisplay.js'
import MarketDetailLine from './MarketDetailLine.vue'

const props = defineProps({
  section: { type: Object, required: true },
})

const items = computed(() => marketSectionDisplayItems(props.section))
const lines = computed(() => items.value.map(item => marketDetailLine(item, props.section.tone)).filter(Boolean))
const visible = computed(() => {
  if (!items.value.length && /市场概况|竞价情绪/.test(props.section.title || '')) return false
  return Boolean(items.value.length || props.section.meta)
})
const wide = computed(() => Boolean(
  props.section.wide
    || /热门板块|竞价强势板块|资金流向|竞价成交活跃/.test(props.section.title || ''),
))
</script>

<template>
  <section v-if="visible" class="market-section" :class="[section.tone || '', { wide }]">
    <div class="market-section-head">
      <div class="market-section-title-wrap">
        <span class="market-section-icon">{{ section.icon || '•' }}</span>
        <span class="market-section-title">{{ section.title || '盘面小节' }}</span>
      </div>
      <span v-if="section.meta || items.length" class="market-section-count">
        {{ section.meta || `${items.length} 条` }}
      </span>
    </div>
    <div v-if="lines.length" class="market-section-body">
      <MarketDetailLine v-for="(line, index) in lines" :key="index" :line="line" />
    </div>
  </section>
</template>
