<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { clampXImageZoom, xMediaDisplayUrl } from '../../utils/xMonitorDisplay.js'

const props = defineProps({
  url: { type: String, default: '' },
  label: { type: String, default: '推文图片' },
})
const emit = defineEmits(['close'])
const zoom = ref(1)
const title = computed(() => `${props.label || '推文图片'} · ${Math.round(zoom.value * 100)}%`)

function changeZoom(delta) {
  zoom.value = clampXImageZoom(zoom.value + delta)
}

function handleKeydown(event) {
  if (!props.url) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  } else if (event.key === '+' || event.key === '=') {
    event.preventDefault()
    changeZoom(0.25)
  } else if (event.key === '-') {
    event.preventDefault()
    changeZoom(-0.25)
  }
}

watch(() => props.url, url => {
  zoom.value = 1
  document.body.classList.toggle('x-image-viewer-open', Boolean(url))
}, { immediate: true })

onMounted(() => document.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => {
  document.body.classList.remove('x-image-viewer-open')
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="url" class="x-image-viewer-backdrop" @click.self="emit('close')">
      <div class="x-image-viewer-card" role="dialog" aria-modal="true" :aria-label="label || '推文图片'">
        <div class="x-image-viewer-head">
          <div class="x-image-viewer-title">{{ title }}</div>
          <div class="x-image-viewer-actions">
            <button type="button" class="x-image-viewer-btn" title="缩小" aria-label="缩小" :disabled="zoom <= 0.5" @click="changeZoom(-0.25)">-</button>
            <button type="button" class="x-image-viewer-btn" title="放大" aria-label="放大" :disabled="zoom >= 3" @click="changeZoom(0.25)">+</button>
            <button type="button" class="x-image-viewer-btn" title="关闭" aria-label="关闭" @click="emit('close')">x</button>
          </div>
        </div>
        <div class="x-image-viewer-stage">
          <img
            class="x-image-viewer-img"
            :src="xMediaDisplayUrl(url)"
            :alt="label || '推文图片'"
            :style="{ '--x-image-zoom': zoom }"
            draggable="false"
            @click="emit('close')"
          >
        </div>
      </div>
    </div>
  </Teleport>
</template>
