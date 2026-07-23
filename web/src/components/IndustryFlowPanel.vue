<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useIndicesData } from '../composables/useIndicesData.js'
import { useIndustryFlowData } from '../composables/useIndustryFlowData.js'
import { previousDayMarketLabel } from '../utils/marketDisplay.js'
import {
  barScale,
  signedYi,
  useIndustryFlowAnimation,
} from '../composables/useIndustryFlowAnimation.js'

const { state, activateIndustryFlow, deactivateIndustryFlow } = useIndustryFlowData()
const router = useRouter()
const { view } = useIndicesData()
const payload = computed(() => state.payload || {})
const {
  animation,
  speedOptions,
  frames,
  actualPlayback,
  sides,
  maximum,
  displayedProgress,
  currentTime,
  start,
  stop,
  toggle,
  replay,
  setSpeed,
  beginSeek,
  seek,
  endSeek,
} = useIndustryFlowAnimation(payload)

const samplingWindows = computed(() => (
  Array.isArray(payload.value.sampling?.windows) && payload.value.sampling.windows.length >= 2
    ? payload.value.sampling.windows
    : [{ start: '09:25', end: '11:31' }, { start: '13:00', end: '15:01' }]
))
const samplingStatus = computed(() => actualPlayback.value
  ? `播放时间 ${currentTime.value || '--'} · ${frames.value.length} 个真实采样点`
  : `采样积累中 · ${Number(payload.value.sampling?.interval_seconds || 60)} 秒/点`)
const progressText = computed(() => actualPlayback.value
  ? `${animation.playing ? '采样' : '已暂停'} ${currentTime.value || '--'}`
  : `${animation.playing ? '播放' : '已暂停'} ${Math.round(displayedProgress.value * 100)}%`)
const previousDayLabel = computed(() => previousDayMarketLabel(payload.value.generated_at))

function selectPanel(panel) {
  if (!['index', 'market', 'market-breadth'].includes(panel)) return
  view.panel = panel
  router.push({ path: '/indices', query: panel === 'index' ? {} : { panel } })
}

function rowLabel(node, role) {
  return `${node.name}：主力${role === 'inflow' ? '净流入' : '净流出'} ${signedYi(node.net_flow_yi)}`
}

function handleSeek(event) {
  seek(event.target.value)
}

function pinLeavingRow(element) {
  const list = element.parentElement
  if (!list) return
  const rowRect = element.getBoundingClientRect()
  const listRect = list.getBoundingClientRect()
  element.style.position = 'absolute'
  element.style.top = `${rowRect.top - listRect.top}px`
  element.style.left = `${rowRect.left - listRect.left}px`
  element.style.width = `${rowRect.width}px`
  element.style.transform = 'none'
  element.style.pointerEvents = 'none'
}

watch(
  () => state.payload,
  async nextPayload => {
    if (!nextPayload.loaded || !nextPayload.nodes?.length) return
    await nextTick()
    start()
  },
)

onMounted(() => activateIndustryFlow())
onBeforeUnmount(() => {
  stop()
  deactivateIndustryFlow()
})
</script>

<template>
  <div class="indices-page">
    <div class="indices-switch" role="group" aria-label="指数行情、资金流动与市场情绪切换">
      <button type="button" class="indices-switch-btn" aria-pressed="false" @click="selectPanel('index')">指数</button>
      <button type="button" class="indices-switch-btn" aria-pressed="false" @click="selectPanel('market')">行情</button>
      <button type="button" class="indices-switch-btn active" aria-pressed="true">资金流动</button>
      <button type="button" class="indices-switch-btn" aria-pressed="false" @click="selectPanel('market-breadth')">市场情绪</button>
    </div>

    <div v-if="payload.loading && !payload.loaded" class="industry-flow-loading">正在加载行业资金流…</div>
    <section v-else-if="!payload.nodes?.length" class="sector-cloud industry-flow-empty">
      <h2>行业主力资金流</h2>
      <div class="empty">行业主力资金流暂不可用{{ payload.error ? `：${payload.error}` : '' }}</div>
    </section>
    <div v-else class="industry-flow-page">
      <div v-if="payload.stale_cache" class="industry-flow-notice">当前展示最近一次有效快照，后台数据源正在重试。</div>
      <div v-if="payload.error" class="industry-flow-notice warning">实时更新失败，继续展示可用快照：{{ payload.error }}</div>
      <section class="industry-flow-hero">
        <div class="industry-flow-heading">
          <div><h2>行业主力资金流动</h2></div>
          <div class="industry-flow-meta">
            <span v-if="previousDayLabel" class="previous-day-data-badge">{{ previousDayLabel }}</span>
            <span>更新 {{ payload.generated_at || '--' }}</span>
            <span>{{ payload.source || '行业主力净额即时快照' }}</span>
            <span id="industryFlowSampleTime">{{ samplingStatus }}</span>
          </div>
        </div>
        <div class="industry-flow-toolbar">
          <div class="industry-flow-controls">
            <button id="industryFlowPlay" type="button" :aria-pressed="animation.playing" @click="toggle">{{ animation.playing ? '暂停' : '播放' }}</button>
            <button type="button" @click="replay">重播</button>
            <label>
              速度
              <select aria-label="资金流动画速度" :value="animation.speed" @change="setSpeed($event.target.value)">
                <option v-for="speed in speedOptions" :key="speed" :value="speed">{{ speed }}x</option>
              </select>
            </label>
          </div>
        </div>
        <div
          id="industryFlowStage"
          class="flow-bars-stage"
          :data-actual-playback="actualPlayback"
          role="img"
          aria-label="行业主力资金中心对称条形图：左侧主力净流出，右侧主力净流入，均按主力净额绝对值从大到小排序"
        >
          <div class="flow-bars-split">
            <div class="flow-bars-col outflow">
              <TransitionGroup v-if="sides.outflow.length" tag="div" name="industry-flow-rank" class="flow-bars-col-list" data-flow-out-list @before-leave="pinLeavingRow">
                <div
                  v-for="(node, index) in sides.outflow"
                  :key="node.id"
                  class="flow-bar-row outflow"
                  :data-flow-node-id="node.id"
                  data-flow-role="outflow"
                  :style="{ order: index }"
                  :aria-label="rowLabel(node, 'outflow')"
                >
                  <div class="flow-bar-meta">
                    <span class="flow-bar-name" data-flow-name>{{ node.name }}</span>
                    <b class="flow-bar-value" data-flow-value>{{ signedYi(node.net_flow_yi) }}</b>
                  </div>
                  <span class="flow-bar-track"><i data-flow-bar :style="{ transform: `scaleX(${barScale(node.net_flow_yi, maximum).toFixed(4)})` }" /></span>
                </div>
              </TransitionGroup>
              <div v-else class="flow-bars-col-list" data-flow-out-list><div class="flow-bars-empty">暂无净流出板块</div></div>
            </div>
            <div class="flow-bars-axis" aria-hidden="true"><span /></div>
            <div class="flow-bars-col inflow">
              <TransitionGroup v-if="sides.inflow.length" tag="div" name="industry-flow-rank" class="flow-bars-col-list" data-flow-in-list @before-leave="pinLeavingRow">
                <div
                  v-for="(node, index) in sides.inflow"
                  :key="node.id"
                  class="flow-bar-row inflow"
                  :data-flow-node-id="node.id"
                  data-flow-role="inflow"
                  :style="{ order: index }"
                  :aria-label="rowLabel(node, 'inflow')"
                >
                  <div class="flow-bar-meta">
                    <span class="flow-bar-name" data-flow-name>{{ node.name }}</span>
                    <b class="flow-bar-value" data-flow-value>{{ signedYi(node.net_flow_yi) }}</b>
                  </div>
                  <span class="flow-bar-track"><i data-flow-bar :style="{ transform: `scaleX(${barScale(node.net_flow_yi, maximum).toFixed(4)})` }" /></span>
                </div>
              </TransitionGroup>
              <div v-else class="flow-bars-col-list" data-flow-in-list><div class="flow-bars-empty">暂无净流入板块</div></div>
            </div>
          </div>
        </div>
        <div class="industry-flow-progress" aria-label="动画进度">
          <div class="industry-flow-progress-main">
            <div class="industry-flow-progress-track">
              <span id="industryFlowProgressBar" :style="{ width: `${(displayedProgress * 100).toFixed(1)}%` }" />
              <input
                id="industryFlowSeek"
                class="industry-flow-progress-seek"
                type="range"
                min="0"
                max="1000"
                step="1"
                :value="Math.round(displayedProgress * 1000)"
                aria-label="拖动资金流播放进度"
                :aria-valuetext="progressText"
                @pointerdown="beginSeek"
                @pointerup="endSeek"
                @pointercancel="endSeek"
                @keydown="beginSeek"
                @keyup="endSeek"
                @blur="endSeek"
                @input="handleSeek"
              >
            </div>
            <div class="industry-flow-progress-times" aria-label="采样时间段">
              <span>{{ samplingWindows[0]?.start || '--' }}</span>
              <span>{{ samplingWindows[0]?.end || '--' }} / {{ samplingWindows[1]?.start || '--' }}</span>
              <span>{{ samplingWindows[1]?.end || '--' }}</span>
            </div>
          </div>
          <span id="industryFlowProgressText">{{ progressText }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.flow-bars-col-list { position: relative; }
.industry-flow-rank-move,
.industry-flow-rank-enter-active {
  transition: transform 420ms cubic-bezier(.22,.8,.24,1), opacity 302ms cubic-bezier(.22,.8,.24,1);
}
.industry-flow-rank-leave-active {
  transition: opacity 180ms ease-out;
}
.industry-flow-rank-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.industry-flow-rank-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .industry-flow-rank-move,
  .industry-flow-rank-enter-active,
  .industry-flow-rank-leave-active { transition: none; }
}
</style>
