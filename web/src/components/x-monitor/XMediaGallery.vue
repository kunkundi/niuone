<script setup>
import { xMediaDisplayUrl } from '../../utils/xMonitorDisplay.js'

defineProps({
  groups: { type: Array, default: () => [] },
})
const emit = defineEmits(['open-image'])
</script>

<template>
  <div v-if="groups.length" class="x-media-gallery">
    <div v-for="group in groups" :key="group.key" class="x-media-group">
      <div class="x-media-label">{{ group.label }}</div>
      <div class="x-media-grid">
        <button
          v-for="item in group.items"
          :key="item.url"
          type="button"
          class="x-media-tile"
          title="查看图片"
          @click.stop="emit('open-image', item.url, group.label)"
        >
          <span class="x-media-frame">
            <img
              :src="xMediaDisplayUrl(item.url)"
              data-x-media-request="1"
              :alt="group.label"
              loading="lazy"
              fetchpriority="low"
              decoding="async"
            >
          </span>
        </button>
      </div>
    </div>
  </div>
</template>
