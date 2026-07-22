<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  payload: { type: Object, required: true },
})

const SERIES = [
  { key: 'limit_down', label: '跌停板', color: '#16a34a', axis: 'right', group: 'limit' },
  { key: 'limit_up', label: '涨停板', color: '#e11d48', axis: 'right', group: 'limit' },
  { key: 'broken_limit', label: '炸板', color: '#f59e0b', axis: 'right', group: 'limit' },
  { key: 'red', label: '红盘', color: '#fb7185', axis: 'left', group: 'breadth', muted: true },
  { key: 'green', label: '绿盘', color: '#2dd4bf', axis: 'left', group: 'breadth', muted: true },
]

const showBreadth = ref(true)
const showLimitState = ref(true)
const hoveredAt = ref('')
const chartElement = ref(null)

function numeric(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

function tradeProgress(value) {
  const match = String(value || '').match(/(\d{2}):(\d{2})/)
  if (!match) return null
  const minute = Number(match[1]) * 60 + Number(match[2])
  const morningStart = 9 * 60 + 30
  const morningEnd = 11 * 60 + 30
  const afternoonStart = 13 * 60
  const afternoonEnd = 15 * 60
  if (minute < morningStart || minute > afternoonEnd || (minute > morningEnd && minute < afternoonStart)) return null
  return minute <= morningEnd ? minute - morningStart : 120 + minute - afternoonStart
}

function roundedCeiling(value, step, minimum) {
  return Math.max(minimum, Math.ceil(value / step) * step)
}

const latest = computed(() => props.payload.latest || {})
const timeline = computed(() => (Array.isArray(props.payload.timeline) ? props.payload.timeline : [])
  .filter(point => tradeProgress(point.generated_at) != null))
const visibleSeries = computed(() => SERIES.filter(series => (
  series.group === 'breadth' ? showBreadth.value : showLimitState.value
)))
const drawSeries = computed(() => [
  ...visibleSeries.value.filter(series => series.muted),
  ...visibleSeries.value.filter(series => !series.muted),
])
const hasSelection = computed(() => showBreadth.value || showLimitState.value)
const chartAriaLabel = computed(() => {
  if (showBreadth.value && showLimitState.value) return '跌停板、涨停板、炸板、红盘和绿盘数量日内曲线'
  if (showBreadth.value) return '红盘和绿盘数量日内曲线'
  return '跌停板、涨停板和炸板数量日内曲线'
})
const axisHint = computed(() => {
  if (showBreadth.value && showLimitState.value) return '左轴看红绿盘，右轴看涨跌停与炸板'
  if (showBreadth.value) return '当前显示左轴红绿盘'
  if (showLimitState.value) return '当前显示右轴涨跌停与炸板'
  return ''
})

const chart = computed(() => {
  if (!timeline.value.length || !hasSelection.value) return null
  const width = 720
  const height = 286
  const margin = { top: 16, right: 50, bottom: 34, left: 50 }
  const plotWidth = width - margin.left - margin.right
  const plotHeight = height - margin.top - margin.bottom
  const leftPeak = Math.max(
    numeric(latest.value.quote_count),
    ...timeline.value.flatMap(point => [numeric(point.red), numeric(point.green)]),
  )
  const rightPeak = Math.max(
    1,
    ...timeline.value.flatMap(point => [
      numeric(point.limit_down),
      numeric(point.limit_up),
      numeric(point.broken_limit),
    ]),
  )
  const leftMax = roundedCeiling(leftPeak * 1.04, 500, 1000)
  const rightStep = rightPeak > 100 ? 20 : rightPeak > 40 ? 10 : 5
  const rightMax = roundedCeiling(rightPeak * 1.15, rightStep, 10)
  const x = point => margin.left + tradeProgress(point.generated_at) / 240 * plotWidth
  const y = (value, axis) => margin.top + plotHeight
    - numeric(value) / (axis === 'left' ? leftMax : rightMax) * plotHeight
  const paths = drawSeries.value.map(series => ({
    ...series,
    path: timeline.value.map((point, index) => (
      `${index ? 'L' : 'M'}${x(point).toFixed(1)} ${y(point[series.key], series.axis).toFixed(1)}`
    )).join(' '),
    lastX: x(timeline.value.at(-1)).toFixed(1),
    lastY: y(timeline.value.at(-1)[series.key], series.axis).toFixed(1),
  }))
  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4
    return {
      y: (margin.top + plotHeight - ratio * plotHeight).toFixed(1),
      left: Math.round(leftMax * ratio).toLocaleString('zh-CN'),
      right: Math.round(rightMax * ratio).toLocaleString('zh-CN'),
    }
  })
  const xTicks = [
    { minute: 0, label: '09:30' },
    { minute: 60, label: '10:30' },
    { minute: 120, label: '11:30 / 13:00' },
    { minute: 180, label: '14:00' },
    { minute: 240, label: '15:00' },
  ].map(item => ({
    ...item,
    x: (margin.left + item.minute / 240 * plotWidth).toFixed(1),
  }))
  const samples = timeline.value.map(point => ({ point, x: x(point) }))
  return { width, height, margin, plotWidth, plotHeight, paths, grid, xTicks, samples, y }
})

const hoveredSample = computed(() => {
  const current = chart.value
  if (!current || !hoveredAt.value) return null
  const sample = current.samples.find(item => item.point.generated_at === hoveredAt.value)
  if (!sample) return null
  const tooltipWidth = 138
  const rows = visibleSeries.value.map(series => ({
    ...series,
    value: numeric(sample.point[series.key]),
  }))
  const tooltipHeight = 42 + rows.length * 14
  const plotRight = current.width - current.margin.right
  const tooltipX = sample.x + tooltipWidth + 10 <= plotRight
    ? sample.x + 10
    : sample.x - tooltipWidth - 10
  return {
    x: sample.x,
    time: String(sample.point.generated_at || '').slice(11, 19),
    tooltipX,
    tooltipY: current.margin.top + 8,
    tooltipWidth,
    tooltipHeight,
    rows,
    markers: visibleSeries.value.map(series => ({
      ...series,
      y: current.y(sample.point[series.key], series.axis),
    })),
  }
})

function updateHover(event) {
  const current = chart.value
  const svg = event.currentTarget.ownerSVGElement
  if (!current || !svg) return
  const bounds = svg.getBoundingClientRect()
  if (!bounds.width) return
  const pointerX = (event.clientX - bounds.left) * current.width / bounds.width
  const boundedX = Math.max(
    current.margin.left,
    Math.min(current.width - current.margin.right, pointerX),
  )
  let nearest = current.samples[0]
  for (const sample of current.samples) {
    if (Math.abs(sample.x - boundedX) < Math.abs(nearest.x - boundedX)) nearest = sample
  }
  hoveredAt.value = String(nearest?.point?.generated_at || '')
}

function clearHover() {
  hoveredAt.value = ''
}

function clearHoverOutside(event) {
  if (!hoveredAt.value) return
  const bounds = chartElement.value?.getBoundingClientRect()
  if (
    !bounds
    || event.clientX < bounds.left
    || event.clientX > bounds.right
    || event.clientY < bounds.top
    || event.clientY > bounds.bottom
  ) {
    clearHover()
  }
}

onMounted(() => window.addEventListener('pointermove', clearHoverOutside, { passive: true }))
onBeforeUnmount(() => window.removeEventListener('pointermove', clearHoverOutside))

const latestTime = computed(() => String(props.payload.generated_at || latest.value.generated_at || '').slice(11, 19))
</script>

<template>
  <section class="market-breadth-card" aria-labelledby="market-breadth-title">
    <div class="market-breadth-head">
      <div>
        <h3 id="market-breadth-title">A股市场情绪曲线</h3>
        <p>{{ payload.universe || '沪深A股' }} · 每分钟真实采样</p>
      </div>
      <span v-if="latestTime" class="market-breadth-time">{{ latestTime }}</span>
    </div>

    <div v-if="payload.error" class="market-breadth-notice" role="status">
      行情源暂时不可用，{{ payload.stale_cache ? '继续展示上一份有效采样' : '等待下一次采样' }}
    </div>

    <div class="market-breadth-controls" role="group" aria-label="市场情绪曲线显示设置">
      <label class="market-breadth-toggle" :class="{ active: showBreadth }">
        <input v-model="showBreadth" type="checkbox" @change="clearHover">
        <span>红盘 / 绿盘</span>
        <small>左轴</small>
      </label>
      <label class="market-breadth-toggle" :class="{ active: showLimitState }">
        <input v-model="showLimitState" type="checkbox" @change="clearHover">
        <span>涨跌停 / 炸板</span>
        <small>右轴</small>
      </label>
    </div>

    <div v-if="chart" class="market-breadth-legend" aria-label="市场情绪最新统计">
      <div v-for="series in visibleSeries" :key="series.key" class="market-breadth-legend-item">
        <span class="market-breadth-swatch" :style="{ backgroundColor: series.color }"></span>
        <span>{{ series.label }}</span>
        <strong>{{ numeric(latest[series.key]).toLocaleString('zh-CN') }}</strong>
      </div>
    </div>

    <div v-if="chart" class="market-breadth-chart-wrap">
      <svg
        ref="chartElement"
        class="market-breadth-chart"
        :viewBox="`0 0 ${chart.width} ${chart.height}`"
        role="img"
        :aria-label="chartAriaLabel"
        shape-rendering="geometricPrecision"
        @pointerleave="clearHover"
      >
        <g v-for="line in chart.grid" :key="line.y">
          <line
            class="market-breadth-grid"
            :x1="chart.margin.left"
            :x2="chart.width - chart.margin.right"
            :y1="line.y"
            :y2="line.y"
          />
          <text v-if="showBreadth" class="market-breadth-axis-label" :x="chart.margin.left - 8" :y="Number(line.y) + 4" text-anchor="end">{{ line.left }}</text>
          <text v-if="showLimitState" class="market-breadth-axis-label" :x="chart.width - chart.margin.right + 8" :y="Number(line.y) + 4">{{ line.right }}</text>
        </g>
        <g v-for="tick in chart.xTicks" :key="tick.label">
          <line
            class="market-breadth-grid market-breadth-grid-vertical"
            :x1="tick.x"
            :x2="tick.x"
            :y1="chart.margin.top"
            :y2="chart.height - chart.margin.bottom"
          />
          <text class="market-breadth-axis-label" :x="tick.x" :y="chart.height - 10" text-anchor="middle">{{ tick.label }}</text>
        </g>
        <line
          v-if="showBreadth"
          class="market-breadth-axis-line"
          :x1="chart.margin.left"
          :x2="chart.margin.left"
          :y1="chart.margin.top"
          :y2="chart.height - chart.margin.bottom"
        />
        <line
          v-if="showLimitState"
          class="market-breadth-axis-line"
          :x1="chart.width - chart.margin.right"
          :x2="chart.width - chart.margin.right"
          :y1="chart.margin.top"
          :y2="chart.height - chart.margin.bottom"
        />
        <text v-if="showBreadth" class="market-breadth-axis-title" :x="chart.margin.left" y="11">红盘 / 绿盘（只）</text>
        <text v-if="showLimitState" class="market-breadth-axis-title" :x="chart.width - chart.margin.right" y="11" text-anchor="end">涨跌停 / 炸板（只）</text>
        <g v-for="series in chart.paths" :key="series.key">
          <path
            class="market-breadth-line"
            :class="{ 'market-breadth-line-muted': series.muted }"
            :d="series.path"
            :stroke="series.color"
          />
          <circle
            class="market-breadth-endpoint"
            :class="{ 'market-breadth-endpoint-muted': series.muted }"
            :cx="series.lastX"
            :cy="series.lastY"
            :r="series.muted ? 1.45 : 1.9"
            :fill="series.color"
          >
            <title>{{ series.label }} {{ numeric(latest[series.key]).toLocaleString('zh-CN') }} 只</title>
          </circle>
        </g>
        <rect
          class="market-breadth-hit-area"
          :x="chart.margin.left"
          :y="chart.margin.top"
          :width="chart.plotWidth"
          :height="chart.plotHeight"
          aria-hidden="true"
          @pointermove="updateHover"
          @pointerdown="updateHover"
          @pointerleave="clearHover"
        />
        <g v-if="hoveredSample" class="market-breadth-hover" aria-hidden="true">
          <line
            class="market-breadth-crosshair"
            :x1="hoveredSample.x"
            :x2="hoveredSample.x"
            :y1="chart.margin.top"
            :y2="chart.height - chart.margin.bottom"
          />
          <circle
            v-for="marker in hoveredSample.markers"
            :key="marker.key"
            class="market-breadth-hover-point"
            :cx="hoveredSample.x"
            :cy="marker.y"
            r="2.2"
            :fill="marker.color"
          />
          <g :transform="`translate(${hoveredSample.tooltipX} ${hoveredSample.tooltipY})`">
            <rect
              class="market-breadth-tooltip-panel"
              :width="hoveredSample.tooltipWidth"
              :height="hoveredSample.tooltipHeight"
              rx="7"
            />
            <text class="market-breadth-tooltip-time" x="10" y="17">{{ hoveredSample.time }}</text>
            <line class="market-breadth-tooltip-divider" x1="10" :x2="hoveredSample.tooltipWidth - 10" y1="25" y2="25" />
            <g
              v-for="(row, index) in hoveredSample.rows"
              :key="row.key"
              :transform="`translate(0 ${39 + index * 14})`"
            >
              <circle cx="11" cy="0" r="2.1" :fill="row.color" />
              <text class="market-breadth-tooltip-label" x="18" y="3">{{ row.label }}</text>
              <text class="market-breadth-tooltip-value" :x="hoveredSample.tooltipWidth - 10" y="3" text-anchor="end">{{ numeric(row.value).toLocaleString('zh-CN') }}</text>
            </g>
          </g>
        </g>
      </svg>
    </div>

    <div v-else class="market-breadth-empty">
      {{ hasSelection ? '市场情绪曲线等待交易时段首个有效采样' : '请至少勾选一组指标' }}
    </div>

    <div class="market-breadth-foot">
      <span>数据源：{{ payload.source || '腾讯证券沪深A股实时行情' }}</span>
      <span v-if="axisHint">{{ axisHint }}</span>
    </div>
  </section>
</template>
