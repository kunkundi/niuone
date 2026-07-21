<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useXMonitorData, X_MONITOR_PAGE_SIZE } from '../composables/useXMonitorData.js'
import { xRecordKey } from '../utils/xMonitorDisplay.js'
import XImageViewer from './x-monitor/XImageViewer.vue'
import XMonitorRow from './x-monitor/XMonitorRow.vue'

const { state, activateXMonitor, deactivateXMonitor, selectXPage } = useXMonitorData()
const panel = ref(null)
const expandedKey = ref('')
const viewer = ref({ url: '', label: '' })
const totalPages = computed(() => Math.max(1, Math.ceil((state.total || state.records.length || 1) / X_MONITOR_PAGE_SIZE)))
const page = computed(() => Math.min(totalPages.value, Math.floor(state.offset / X_MONITOR_PAGE_SIZE) + 1))
const first = computed(() => state.total && state.records.length ? state.offset + 1 : (state.records.length ? 1 : 0))
const last = computed(() => state.total && state.records.length
  ? Math.min(state.offset + state.records.length, state.total)
  : state.records.length)
const latest = computed(() => state.records[0]?.time || '')
const oldest = computed(() => state.records.at(-1)?.time || '')
const atFirst = computed(() => state.offset <= 0)
const atLast = computed(() => state.total
  ? state.offset + X_MONITOR_PAGE_SIZE >= state.total
  : state.records.length < X_MONITOR_PAGE_SIZE)
const lastOffset = computed(() => Math.max(0, (totalPages.value - 1) * X_MONITOR_PAGE_SIZE))

function pageOffsetFromUrl() {
  return Math.max(0, (Number(new URLSearchParams(location.search).get('page') || 1) - 1) * X_MONITOR_PAGE_SIZE)
}

function syncUrl(offset) {
  const next = new URL(location.href)
  const nextPage = Math.floor(offset / X_MONITOR_PAGE_SIZE) + 1
  if (nextPage > 1) next.searchParams.set('page', String(nextPage))
  else next.searchParams.delete('page')
  history.replaceState(null, '', `${next.pathname}${next.search}${next.hash}`)
}

function cancelPendingMedia() {
  panel.value?.querySelectorAll('img[data-x-media-request]').forEach(image => {
    if (!image.complete) image.removeAttribute('src')
  })
}

async function selectPage(offset) {
  const target = Math.max(0, Math.min(Number(offset || 0), lastOffset.value))
  if (target === state.offset || state.loading) return
  const previous = state.offset
  cancelPendingMedia()
  expandedKey.value = ''
  viewer.value = { url: '', label: '' }
  syncUrl(target)
  const loaded = await selectXPage(target)
  if (!loaded) syncUrl(previous)
}

function toggleRow(key) {
  expandedKey.value = expandedKey.value === key ? '' : key
}

function openImage(url, label = '推文图片') {
  viewer.value = { url, label }
}

function handlePopstate() {
  cancelPendingMedia()
  expandedKey.value = ''
  viewer.value = { url: '', label: '' }
  selectXPage(pageOffsetFromUrl())
}

onMounted(() => {
  window.addEventListener('popstate', handlePopstate)
  activateXMonitor(pageOffsetFromUrl())
})
onBeforeUnmount(() => {
  window.removeEventListener('popstate', handlePopstate)
  cancelPendingMedia()
  deactivateXMonitor()
})
</script>

<template>
  <div ref="panel">
    <div v-if="state.loading && !state.loaded" class="loading">加载中…</div>
    <template v-else-if="!state.records.length">
      <div v-if="state.error" class="industry-flow-notice warning">推特监控加载失败：{{ state.error }}</div>
      <div class="empty">暂无推特监控消息</div>
    </template>
    <template v-else>
      <div v-if="state.error" class="industry-flow-notice warning">
        自动更新暂时失败，继续展示已缓存推文：{{ state.error }}
      </div>
      <section class="sector-cloud x-monitor-panel">
        <div class="x-monitor-head">
          <div>
            <div class="x-monitor-title">推特监控流</div>
            <div class="x-monitor-sub">
              {{ latest ? `最新 ${latest}` : '等待监控数据' }}{{ oldest ? ` · 最早 ${oldest}` : '' }}
            </div>
          </div>
          <div class="x-monitor-metrics">
            <span class="x-metric">第 {{ page }} / {{ totalPages }} 页</span>
            <span class="x-metric">本页 {{ state.records.length }}</span>
          </div>
        </div>
        <div class="x-list">
          <XMonitorRow
            v-for="record in state.records"
            :key="xRecordKey(record)"
            :record="record"
            :expanded="expandedKey === xRecordKey(record)"
            @toggle="toggleRow"
            @open-image="openImage"
          />
        </div>
      </section>
      <div class="sector-cloud x-pager">
        <div class="x-pager-status">
          第 {{ page }} / {{ totalPages }} 页 · {{ first }}-{{ last }} / {{ state.total || last }} 条{{ state.loading ? ' · 加载中...' : '' }}
        </div>
        <div class="x-pager-actions">
          <button type="button" class="x-page-btn" :disabled="state.loading || atFirst" @click="selectPage(0)">首页</button>
          <button type="button" class="x-page-btn" :disabled="state.loading || atFirst" @click="selectPage(state.offset - X_MONITOR_PAGE_SIZE)">上一页</button>
          <button type="button" class="x-page-btn" :disabled="state.loading || atLast" @click="selectPage(state.offset + X_MONITOR_PAGE_SIZE)">下一页</button>
          <button type="button" class="x-page-btn" :disabled="state.loading || atLast" @click="selectPage(lastOffset)">末页</button>
        </div>
      </div>
    </template>
    <XImageViewer :url="viewer.url" :label="viewer.label" @close="viewer = { url: '', label: '' }" />
  </div>
</template>
