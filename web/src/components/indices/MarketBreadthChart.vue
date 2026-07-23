<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { previousDayMarketLabel } from '../../utils/marketDisplay.js'

const props = defineProps({
  payload: { type: Object, required: true },
})

const SERIES = [
  { key: 'limit_down', label: '跌停板', color: 'var(--market-breadth-limit-down, #4ade80)', axis: 'right', group: 'limit' },
  { key: 'limit_up', label: '涨停板', color: 'var(--market-breadth-limit-up, #fb7185)', axis: 'right', group: 'limit' },
  { key: 'broken_limit', label: '炸板', color: 'var(--market-breadth-broken-limit, #fbbf24)', axis: 'right', group: 'limit' },
  { key: 'red', label: '红盘', color: 'var(--market-breadth-red, #e879f9)', axis: 'left', group: 'breadth', muted: true },
  { key: 'green', label: '绿盘', color: 'var(--market-breadth-green, #38bdf8)', axis: 'left', group: 'breadth', muted: true },
  { key: 'estimated_turnover_yi', label: '预测全天量能', color: 'var(--market-breadth-estimated-turnover, #f59e0b)', axis: 'volume', group: 'volume' },
  { key: 'actual_turnover_yi', label: '今日实际量能', color: 'var(--market-breadth-actual-turnover, #818cf8)', axis: 'volume', group: 'volume', muted: true },
  { key: 'previous_actual_turnover_yi', label: '前日同期量能', color: 'var(--market-breadth-previous-turnover, #94a3b8)', axis: 'volume', group: 'volume', muted: true, dashed: true },
  { key: 'turnover_increment_yi', label: '预测增量', color: 'var(--market-breadth-turnover-increment, #2dd4bf)', axis: 'volume', group: 'volume', signed: true },
  { key: 'turnover_same_time_delta_yi', label: '同时点量能差', color: 'var(--market-breadth-same-time-delta, #22d3ee)', axis: 'volume', group: 'volume', signed: true },
]

const showBreadth = ref(true)
const showLimitState = ref(true)
const showVolume = ref(true)
const hoveredAt = ref('')
const chartElement = ref(null)
const chartWrapElement = ref(null)
const chartWidth = ref(720)
const chartAvailableHeight = ref(330)
let chartResizeObserver = null

function nullableNumeric(value, allowNegative = false) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && (allowNegative || parsed >= 0) ? parsed : null
}

function numeric(value) {
  return nullableNumeric(value) ?? 0
}

function formatSeriesValue(series, value, withCountUnit = false) {
  const parsed = nullableNumeric(value, series.signed)
  if (parsed == null) return '--'
  if (series.axis === 'volume') {
    const sign = series.signed && parsed > 0 ? '+' : ''
    const absolute = Math.abs(parsed)
    if (absolute >= 10_000) {
      return `${sign}${(parsed / 10_000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}万亿`
    }
    return `${sign}${parsed.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}亿`
  }
  const formatted = Math.round(parsed).toLocaleString('zh-CN')
  return withCountUnit ? `${formatted}只` : formatted
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

function turnoverStep(value) {
  if (value > 30_000) return 5_000
  if (value > 15_000) return 2_000
  if (value > 5_000) return 1_000
  if (value > 1_000) return 500
  return 100
}

function spreadEndLabels(paths, top, bottom) {
  const gap = 17
  const labels = paths
    .map(path => ({
      ...path,
      anchorY: Number(path.lastY),
      labelY: Number(path.lastY),
    }))
    .sort((left, right) => left.labelY - right.labelY)
  if (!labels.length) return labels

  labels[0].labelY = Math.max(top, labels[0].labelY)
  for (let index = 1; index < labels.length; index += 1) {
    labels[index].labelY = Math.max(labels[index].labelY, labels[index - 1].labelY + gap)
  }
  const overflow = labels.at(-1).labelY - bottom
  if (overflow > 0) labels.forEach(label => { label.labelY -= overflow })
  for (let index = labels.length - 2; index >= 0; index -= 1) {
    labels[index].labelY = Math.min(labels[index].labelY, labels[index + 1].labelY - gap)
  }
  const underflow = top - labels[0].labelY
  if (underflow > 0) labels.forEach(label => { label.labelY += underflow })
  return labels
}

const latest = computed(() => props.payload.latest || {})
const turnoverComparison = computed(() => props.payload.turnover_comparison || {})
const turnoverActual = computed(() => props.payload.turnover_actual || {})
const turnoverPreviousActual = computed(() => props.payload.turnover_previous_actual || {})
const turnoverEstimate = computed(() => props.payload.turnover_estimate || {})
const timeline = computed(() => (Array.isArray(props.payload.timeline) ? props.payload.timeline : [])
  .filter(point => tradeProgress(point.generated_at) != null))
const visibleSeries = computed(() => SERIES.filter(series => (
  series.group === 'breadth'
    ? showBreadth.value
    : series.group === 'limit'
      ? showLimitState.value
      : showVolume.value
)))
const drawSeries = computed(() => [
  ...visibleSeries.value.filter(series => series.muted),
  ...visibleSeries.value.filter(series => !series.muted),
])
const hasSelection = computed(() => showBreadth.value || showLimitState.value || showVolume.value)
const chartAriaLabel = computed(() => `${visibleSeries.value.map(series => series.label).join('、')}日内曲线`)
const axisHint = computed(() => {
  const hints = []
  if (showBreadth.value) hints.push('左轴红绿盘')
  if (showLimitState.value) hints.push('右轴涨跌停与炸板')
  if (showVolume.value) hints.push('下方量能区间（亿元）')
  return hints.length ? `当前显示：${hints.join('，')}` : ''
})

const chart = computed(() => {
  if (!timeline.value.length || !hasSelection.value) return null
  const width = chartWidth.value
  const compact = width < 560
  const showSentiment = showBreadth.value || showLimitState.value
  const baseHeight = showSentiment && showVolume.value ? (compact ? 280 : 330) : (compact ? 218 : 236)
  const compactMinHeight = showSentiment && showVolume.value ? 220 : 180
  const height = compact
    ? Math.max(compactMinHeight, Math.min(baseHeight, chartAvailableHeight.value))
    : Math.max(baseHeight, chartAvailableHeight.value)
  const margin = compact
    ? { top: 16, right: 88, bottom: 30, left: 42 }
    : { top: 16, right: 92, bottom: 34, left: 50 }
  const plotWidth = width - margin.left - margin.right
  const sectionGap = showSentiment && showVolume.value ? (compact ? 20 : 24) : 0
  const drawableHeight = height - margin.top - margin.bottom - sectionGap
  const sentimentHeight = showSentiment
    ? (showVolume.value ? Math.round(drawableHeight * 0.64) : drawableHeight)
    : 0
  const sentimentBottom = margin.top + sentimentHeight
  const volumeTop = showSentiment ? sentimentBottom + sectionGap : margin.top
  const volumeHeight = showVolume.value ? drawableHeight - sentimentHeight : 0
  const plotBottom = showVolume.value ? volumeTop + volumeHeight : sentimentBottom
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
  const volumeValues = timeline.value.flatMap(point => (
    SERIES.filter(series => series.axis === 'volume').flatMap(series => {
      const value = nullableNumeric(point[series.key], series.signed)
      return value == null ? [] : [value]
    })
  ))
  const volumeLow = Math.min(0, ...volumeValues)
  const volumeHigh = Math.max(0, ...volumeValues)
  const volumeMagnitude = Math.max(Math.abs(volumeLow), Math.abs(volumeHigh))
  const volumeStep = turnoverStep(volumeMagnitude)
  const volumeMin = volumeLow < 0 ? Math.floor(volumeLow * 1.06 / volumeStep) * volumeStep : 0
  const volumeMax = roundedCeiling(volumeHigh * 1.06, volumeStep, 100)
  const volumeRange = Math.max(100, volumeMax - volumeMin)
  const x = point => margin.left + tradeProgress(point.generated_at) / 240 * plotWidth
  const y = (value, axis, allowNegative = false) => {
    const parsed = nullableNumeric(value, allowNegative)
    if (parsed == null) return null
    if (axis === 'volume') {
      return volumeTop + (volumeMax - parsed) / volumeRange * volumeHeight
    }
    const axisMax = axis === 'left' ? leftMax : rightMax
    return margin.top + sentimentHeight - parsed / axisMax * sentimentHeight
  }
  const paths = drawSeries.value.map(series => {
    const points = timeline.value.flatMap(point => {
      const value = nullableNumeric(point[series.key], series.signed)
      const pointY = y(value, series.axis, series.signed)
      return value == null || pointY == null ? [] : [{ x: x(point), y: pointY, value }]
    })
    const last = points.at(-1)
    return {
      ...series,
      path: points.map((point, index) => (
        `${index ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`
      )).join(' '),
      lastX: last?.x.toFixed(1),
      lastY: last?.y.toFixed(1),
      lastValue: last?.value,
      labelWidth: Math.max(31, series.label.length * 10 + 9),
    }
  }).filter(series => series.path)
  const latestX = Math.max(
    margin.left,
    ...paths.map(path => Number(path.lastX)),
  )
  const labelRailX = Math.min(latestX + 14, width - margin.right + 11)
  const endLabels = [
    ...spreadEndLabels(
      paths.filter(path => path.axis !== 'volume'),
      margin.top + 6,
      sentimentBottom - 6,
    ),
    ...spreadEndLabels(
      paths.filter(path => path.axis === 'volume'),
      volumeTop + 6,
      plotBottom - 6,
    ),
  ]
  const grid = showSentiment ? Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4
    return {
      y: (sentimentBottom - ratio * sentimentHeight).toFixed(1),
      left: Math.round(leftMax * ratio).toLocaleString('zh-CN'),
      right: Math.round(rightMax * ratio).toLocaleString('zh-CN'),
    }
  }) : []
  const volumeTickValues = volumeMin < 0
    ? [volumeMin, 0, volumeMax]
    : [0, volumeMax / 2, volumeMax]
  const volumeGrid = showVolume.value ? volumeTickValues.map(value => {
    return {
      y: y(value, 'volume', true).toFixed(1),
      value: Math.round(value).toLocaleString('zh-CN'),
      zero: value === 0,
    }
  }) : []
  const xTickSource = compact
    ? [
        { minute: 0, label: '09:30' },
        { minute: 120, label: '11:30 / 13:00' },
        { minute: 240, label: '15:00' },
      ]
    : [
        { minute: 0, label: '09:30' },
        { minute: 60, label: '10:30' },
        { minute: 120, label: '11:30 / 13:00' },
        { minute: 180, label: '14:00' },
        { minute: 240, label: '15:00' },
      ]
  const xTicks = xTickSource.map(item => ({
    ...item,
    x: (margin.left + item.minute / 240 * plotWidth).toFixed(1),
  }))
  const sampleTimes = timeline.value.map(point => String(point.generated_at || '').slice(11, 19))
  const missingMorning = sampleTimes.some(value => value >= '13:00:00')
    && !sampleTimes.some(value => value <= '11:30:59')
  const morningNotice = missingMorning
    ? {
        x: margin.left + plotWidth / 4,
        y: showSentiment
          ? margin.top + sentimentHeight / 2
          : volumeTop + volumeHeight / 2,
      }
    : null
  const samples = timeline.value.map(point => ({ point, x: x(point) }))
  return {
    width,
    height,
    margin,
    plotWidth,
    sentimentBottom,
    volumeTop,
    volumeHeight,
    volumeMin,
    plotBottom,
    paths,
    endLabels,
    labelRailX,
    morningNotice,
    grid,
    volumeGrid,
    xTicks,
    samples,
    y,
  }
})

const hoveredSample = computed(() => {
  const current = chart.value
  if (!current || !hoveredAt.value) return null
  const sample = current.samples.find(item => item.point.generated_at === hoveredAt.value)
  if (!sample) return null
  const tooltipWidth = 166
  const rows = visibleSeries.value.map(series => ({
    ...series,
    value: nullableNumeric(sample.point[series.key], series.signed),
    displayValue: formatSeriesValue(series, sample.point[series.key]),
  }))
  const tooltipHeight = 42 + rows.length * 14
  const plotRight = current.width - current.margin.right
  const tooltipGap = 10
  const tooltipX = sample.x + tooltipWidth + tooltipGap <= plotRight
    ? sample.x + tooltipGap
    : Math.max(current.margin.left, sample.x - tooltipWidth - tooltipGap)
  return {
    x: sample.x,
    time: String(sample.point.generated_at || '').slice(11, 19),
    tooltipX,
    tooltipY: current.margin.top + 8,
    tooltipWidth,
    tooltipHeight,
    rows,
    markers: visibleSeries.value.flatMap(series => {
      const markerY = current.y(sample.point[series.key], series.axis, series.signed)
      return markerY == null ? [] : [{ ...series, y: markerY }]
    }),
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

function syncChartSize() {
  const bounds = chartWrapElement.value?.getBoundingClientRect()
  const availableWidth = Math.round(bounds?.width || 0)
  if (availableWidth > 0) chartWidth.value = Math.max(300, availableWidth)
  const visualViewport = window.visualViewport
  const viewportBottom = Math.floor(
    visualViewport
      ? visualViewport.height + visualViewport.offsetTop
      : window.innerHeight || document.documentElement.clientHeight || 0,
  )
  const bottomReserve = availableWidth < 560 ? 56 : 40
  const availableHeight = Math.floor(viewportBottom - (bounds?.top || 0) - bottomReserve)
  if (availableHeight > 0) chartAvailableHeight.value = availableHeight
}

watch(chartWrapElement, element => {
  chartResizeObserver?.disconnect()
  chartResizeObserver = null
  if (!element) return
  syncChartSize()
  if (typeof ResizeObserver !== 'undefined') {
    chartResizeObserver = new ResizeObserver(syncChartSize)
    chartResizeObserver.observe(element)
  }
}, { flush: 'post' })

onMounted(() => {
  window.addEventListener('pointermove', clearHoverOutside, { passive: true })
  window.addEventListener('resize', syncChartSize, { passive: true })
  window.visualViewport?.addEventListener('resize', syncChartSize, { passive: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('pointermove', clearHoverOutside)
  window.removeEventListener('resize', syncChartSize)
  window.visualViewport?.removeEventListener('resize', syncChartSize)
  chartResizeObserver?.disconnect()
})

const latestGeneratedAt = computed(() => String(
  props.payload.generated_at || latest.value.generated_at || '',
).trim())
const latestTime = computed(() => latestGeneratedAt.value.slice(11, 19))
const previousDayLabel = computed(() => previousDayMarketLabel(
  latestGeneratedAt.value,
))
const turnoverComparisonText = computed(() => {
  const comparison = turnoverComparison.value
  const previous = nullableNumeric(comparison.previous_turnover_yi)
  if (previous == null || !comparison.date) return ''
  const value = formatSeriesValue(SERIES.find(series => series.key === 'actual_turnover_yi'), previous)
  const date = String(comparison.date).slice(5)
  const source = String(comparison.source || '').trim()
  return `增量基准：${date} 全天 ${value}${source ? ` · ${source}` : ''}`
})
const turnoverSourceText = computed(() => {
  const source = String(
    turnoverActual.value.source || latest.value.turnover_actual_source || '',
  ).trim()
  return source ? `实际量能：${source}` : ''
})
const turnoverPreviousText = computed(() => {
  const info = turnoverPreviousActual.value
  const date = String(info.date || '').slice(5)
  if (!date) return ''
  return `前日同期：${date}`
})
const turnoverEstimateText = computed(() => {
  const info = turnoverEstimate.value
  const warning = String(latest.value.turnover_estimate_warning || '').trim()
  if (!info.model && warning) return `量能估算：${warning}`
  const days = Number(info.profile_days || latest.value.turnover_profile_days)
  const interval = Number(info.interval_minutes || latest.value.turnover_profile_interval_minutes)
  const start = String(info.profile_start || latest.value.turnover_profile_start || '').slice(5)
  const end = String(info.profile_end || latest.value.turnover_profile_end || '').slice(5)
  const model = String(
    info.model_label || latest.value.turnover_estimate_model_label || '同分钟成交占比中位数',
  ).trim()
  if (!days) return ''
  const range = start && end ? ` · ${start}—${end}` : ''
  return `量能估算：${model}（${days}日${interval ? ` / ${interval}分钟` : ''}）${range}`
})

</script>

<template>
  <section class="market-breadth-card" aria-labelledby="market-breadth-title">
    <div class="market-breadth-head">
      <div class="market-breadth-heading">
        <div class="market-breadth-title-row">
          <h3 id="market-breadth-title">A股市场情绪曲线</h3>
          <div class="market-breadth-info">
            <button
              class="market-breadth-info-trigger"
              type="button"
              aria-label="查看市场情绪数据说明"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <circle cx="10" cy="10" r="8"></circle>
                <path d="M10 9v5M10 6.2v.1"></path>
              </svg>
            </button>
            <div class="market-breadth-info-popover" role="tooltip">
              <strong>数据说明</strong>
              <span>{{ payload.universe || '沪深A股' }} · 每分钟真实采样</span>
              <span v-if="latestGeneratedAt">最新采样：{{ latestGeneratedAt }}</span>
              <span>情绪数据源：{{ payload.source || '腾讯证券沪深A股实时行情' }}</span>
              <span v-if="showVolume && turnoverSourceText">{{ turnoverSourceText }}</span>
              <span v-if="showVolume && turnoverPreviousText">{{ turnoverPreviousText }}</span>
              <span v-if="showVolume && turnoverEstimateText">{{ turnoverEstimateText }}</span>
              <span v-if="showVolume && turnoverComparisonText">{{ turnoverComparisonText }}</span>
              <span v-if="axisHint">{{ axisHint }}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="market-breadth-controls" role="group" aria-label="市场情绪曲线显示设置">
        <label class="market-breadth-toggle" :class="{ active: showBreadth }">
          <input v-model="showBreadth" type="checkbox" @change="clearHover">
          <span class="market-breadth-label-desktop">红盘 / 绿盘</span>
          <span class="market-breadth-label-mobile">红绿盘</span>
          <small>左轴</small>
        </label>
        <label class="market-breadth-toggle" :class="{ active: showLimitState }">
          <input v-model="showLimitState" type="checkbox" @change="clearHover">
          <span class="market-breadth-label-desktop">涨跌停 / 炸板</span>
          <span class="market-breadth-label-mobile">涨跌停</span>
          <small>右轴</small>
        </label>
        <label class="market-breadth-toggle" :class="{ active: showVolume }">
          <input v-model="showVolume" type="checkbox" @change="clearHover">
          <span class="market-breadth-label-desktop">预测 / 今昨实际 / 差额</span>
          <span class="market-breadth-label-mobile">量能</span>
          <small>亿元</small>
        </label>
      </div>
      <div class="market-breadth-head-meta">
        <span v-if="previousDayLabel" class="previous-day-data-badge">{{ previousDayLabel }}</span>
        <span v-if="latestTime" class="market-breadth-time">{{ latestTime }}</span>
      </div>
    </div>

    <div v-if="payload.error" class="market-breadth-notice" role="status">
      行情源暂时不可用，{{ payload.stale_cache ? '继续展示上一份有效采样' : '等待下一次采样' }}
    </div>

    <div v-if="chart" ref="chartWrapElement" class="market-breadth-chart-wrap">
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
          <text v-if="showLimitState" class="market-breadth-axis-label" :x="chart.width - 7" :y="Number(line.y) + 4" text-anchor="end">{{ line.right }}</text>
        </g>
        <g v-for="tick in chart.xTicks" :key="tick.label">
          <line
            class="market-breadth-grid market-breadth-grid-vertical"
            :x1="tick.x"
            :x2="tick.x"
            :y1="chart.margin.top"
            :y2="chart.plotBottom"
          />
          <text class="market-breadth-axis-label" :x="tick.x" :y="chart.height - 10" text-anchor="middle">{{ tick.label }}</text>
        </g>
        <line
          v-if="showBreadth"
          class="market-breadth-axis-line"
          :x1="chart.margin.left"
          :x2="chart.margin.left"
          :y1="chart.margin.top"
          :y2="chart.sentimentBottom"
        />
        <line
          v-if="showLimitState"
          class="market-breadth-axis-line"
          :x1="chart.width - chart.margin.right"
          :x2="chart.width - chart.margin.right"
          :y1="chart.margin.top"
          :y2="chart.sentimentBottom"
        />
        <text v-if="showBreadth" class="market-breadth-axis-title" :x="chart.margin.left" y="11">红盘 / 绿盘（只）</text>
        <text v-if="showLimitState" class="market-breadth-axis-title" :x="chart.width - chart.margin.right" y="11" text-anchor="end">涨跌停 / 炸板（只）</text>
        <g v-for="line in chart.volumeGrid" :key="`volume-${line.y}`">
          <line
            class="market-breadth-grid market-breadth-volume-grid"
            :class="{ 'market-breadth-volume-grid-zero': line.zero && chart.volumeMin < 0 }"
            :x1="chart.margin.left"
            :x2="chart.width - chart.margin.right"
            :y1="line.y"
            :y2="line.y"
          />
          <text
            class="market-breadth-axis-label"
            :x="chart.margin.left - 8"
            :y="Number(line.y) + 4"
            text-anchor="end"
          >{{ line.value }}</text>
        </g>
        <line
          v-if="showVolume"
          class="market-breadth-axis-line"
          :x1="chart.margin.left"
          :x2="chart.margin.left"
          :y1="chart.volumeTop"
          :y2="chart.plotBottom"
        />
        <text
          v-if="showVolume"
          class="market-breadth-axis-title"
          :x="chart.margin.left"
          :y="chart.volumeTop - 9"
        >市场量能（亿元）</text>
        <g v-if="chart.morningNotice" class="market-breadth-missing-period" aria-hidden="true">
          <rect
            class="market-breadth-missing-period-bg"
            :x="chart.morningNotice.x - 43"
            :y="chart.morningNotice.y - 9"
            width="86"
            height="18"
            rx="6"
          />
          <text
            class="market-breadth-missing-period-text"
            :x="chart.morningNotice.x"
            :y="chart.morningNotice.y + 3"
            text-anchor="middle"
          >上午无有效采样</text>
        </g>
        <g v-for="series in chart.paths" :key="series.key">
          <path
            class="market-breadth-line"
            :class="{
              'market-breadth-line-muted': series.muted,
              'market-breadth-line-dashed': series.dashed,
            }"
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
            <title>{{ series.label }} {{ formatSeriesValue(series, series.lastValue, true) }}</title>
          </circle>
        </g>
        <g
          v-for="label in chart.endLabels"
          :key="`${label.key}-end-label`"
          class="market-breadth-end-label-group"
          aria-hidden="true"
        >
          <path
            class="market-breadth-end-label-connector"
            :d="`M ${Number(label.lastX) + 3} ${label.anchorY} L ${chart.labelRailX - 7} ${label.anchorY} L ${chart.labelRailX - 2} ${label.labelY}`"
            :stroke="label.color"
          />
          <rect
            class="market-breadth-end-label-bg"
            :x="chart.labelRailX"
            :y="label.labelY - 7"
            :width="label.labelWidth"
            height="14"
            rx="4"
          />
          <text
            class="market-breadth-end-label"
            :x="chart.labelRailX + 4"
            :y="label.labelY + 3"
            :fill="label.color"
          >{{ label.label }}</text>
        </g>
        <rect
          class="market-breadth-hit-area"
          :x="chart.margin.left"
          :y="chart.margin.top"
          :width="chart.plotWidth"
          :height="chart.plotBottom - chart.margin.top"
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
            :y2="chart.plotBottom"
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
              <text class="market-breadth-tooltip-value" :x="hoveredSample.tooltipWidth - 10" y="3" text-anchor="end">{{ row.displayValue }}</text>
            </g>
          </g>
        </g>
      </svg>
    </div>

    <div v-else class="market-breadth-empty">
      {{ hasSelection ? '市场情绪曲线等待交易时段首个有效采样' : '请至少勾选一组指标' }}
    </div>

  </section>
</template>
