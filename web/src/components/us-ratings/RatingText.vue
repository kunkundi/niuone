<script setup>
import { computed } from 'vue'
import { ratingTextSegments } from '../../utils/usRatingDisplay.js'

const props = defineProps({
  value: { type: String, default: '' },
})
const segments = computed(() => ratingTextSegments(props.value))
</script>

<template>
  <template v-for="(segment, index) in segments" :key="`${index}-${segment.text}`">
    <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
    <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
    <template v-else>{{ segment.text }}</template>
  </template>
</template>
