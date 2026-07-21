<script setup>
import { computed } from 'vue'
import {
  parseXThread,
  stripXCurrentPostHeader,
  summarizeXRecord,
  xAllMediaItems,
  xMediaDisplayUrl,
  xMediaGroups,
  xRecordKey,
} from '../../utils/xMonitorDisplay.js'
import XMediaGallery from './XMediaGallery.vue'

const props = defineProps({
  record: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle', 'open-image'])
const key = computed(() => xRecordKey(props.record))
const summary = computed(() => summarizeXRecord(props.record))
const mediaGroups = computed(() => xMediaGroups(props.record))
const previewMedia = computed(() => xAllMediaItems(props.record).filter(item => item.url))
const thread = computed(() => parseXThread(props.record?.content || ''))
const replyBody = computed(() => stripXCurrentPostHeader(thread.value.reply) || thread.value.reply || '')
const plainBody = computed(() => stripXCurrentPostHeader(props.record?.content || '') || '（无正文）')
const originalGroups = computed(() => mediaGroups.value.filter(group => ['reply_to_media', 'quoted_media'].includes(group.key)))
const mainGroups = computed(() => mediaGroups.value.filter(group => group.key === 'media'))

function toggle() {
  emit('toggle', key.value)
}

function handleKey(event) {
  if (!['Enter', ' '].includes(event.key)) return
  event.preventDefault()
  toggle()
}

function openImage(url, label) {
  emit('open-image', url, label)
}
</script>

<template>
  <article
    class="x-row"
    :class="{ open: expanded }"
    :data-x-key="key"
    :aria-expanded="expanded"
    role="button"
    tabindex="0"
    @click="toggle"
    @keydown="handleKey"
  >
    <div class="x-avatar">{{ summary.initial }}</div>
    <div class="x-copy">
      <div class="x-line">
        <span class="x-author">{{ summary.author }}</span>
        <span class="x-handle">{{ summary.label }}</span>
        <span v-if="summary.time" class="x-time">{{ summary.time }}</span>
      </div>
      <template v-if="!expanded">
        <div class="x-preview">{{ summary.preview }}</div>
        <div v-if="previewMedia.length" class="x-media-strip">
          <span class="x-media-thumb">
            <img
              :src="xMediaDisplayUrl(previewMedia[0].url)"
              data-x-media-request="1"
              alt="推文图片"
              loading="lazy"
              fetchpriority="low"
              decoding="async"
            >
          </span>
          <span v-if="previewMedia.length > 1" class="x-media-more">+{{ previewMedia.length - 1 }}</span>
        </div>
      </template>
    </div>
    <div class="x-badges"><span class="x-chevron">›</span></div>
    <div v-if="expanded" class="x-detail" @click.stop @keydown.stop>
      <div v-if="thread.originalPost && thread.reply" class="thread-card">
        <div class="thread-reply">
          <div class="thread-reply-content">{{ replyBody }}</div>
          <XMediaGallery :groups="mainGroups" @open-image="openImage" />
        </div>
        <div class="thread-original">
          <div class="thread-original-content">{{ thread.originalPost }}</div>
          <XMediaGallery :groups="originalGroups" @open-image="openImage" />
        </div>
      </div>
      <template v-else>
        <div class="content">{{ plainBody }}</div>
        <XMediaGallery :groups="mediaGroups" @open-image="openImage" />
      </template>
    </div>
  </article>
</template>
