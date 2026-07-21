<script setup>
defineProps({
  line: { type: Object, required: true },
})
</script>

<template>
  <div v-if="line.kind === 'flow'" class="market-detail-line flow">
    <span class="market-flow-label">{{ line.label }}</span>
    <span class="market-flow-value">
      <template v-for="(segment, index) in line.segments" :key="`${index}-${segment.text}`">
        <span v-if="segment.kind === 'symbol'" class="market-symbol">{{ segment.text }}</span>
        <span v-else-if="segment.kind === 'number' && segment.tone" class="market-num" :class="segment.tone">{{ segment.text }}</span>
        <template v-else>{{ segment.text }}</template>
      </template>
    </span>
  </div>
  <div v-else class="market-detail-line item" :class="{ note: line.note, risk: line.risk, tip: line.tip }">
    <span>
      <template v-for="(segment, index) in line.segments" :key="`${index}-${segment.text}`">
        <span v-if="segment.kind === 'symbol'" class="market-symbol">{{ segment.text }}</span>
        <span v-else-if="segment.kind === 'number' && segment.tone" class="market-num" :class="segment.tone">{{ segment.text }}</span>
        <template v-else>{{ segment.text }}</template>
      </template>
    </span>
  </div>
</template>
