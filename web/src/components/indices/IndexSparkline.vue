<script setup>
import { computed } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
})

function clamp01(value) {
  return Math.max(0, Math.min(1, value))
}

function clockMinuteOfDay(timeText) {
  const match = String(timeText || '').match(/(\d{1,2}):(\d{2})/)
  return match ? Number(match[1]) * 60 + Number(match[2]) : null
}

function tradeMinuteOfDay(timeText) {
  const match = String(timeText || '').match(/(\d{2}):(\d{2})/)
  if (!match) return null
  const minutes = Number(match[1]) * 60 + Number(match[2])
  const morningStart = 9 * 60 + 30
  const morningEnd = 11 * 60 + 30
  const afternoonStart = 13 * 60
  const afternoonEnd = 15 * 60
  if (minutes < morningStart || minutes > afternoonEnd || (minutes > morningEnd && minutes < afternoonStart)) return null
  return minutes <= morningEnd ? minutes - morningStart : 120 + minutes - afternoonStart
}

function sessionElapsed(clockMinute, sessionStartMinute) {
  if (clockMinute == null || sessionStartMinute == null) return null
  let elapsed = clockMinute - sessionStartMinute
  if (elapsed < 0) elapsed += 24 * 60
  return elapsed
}

function compressedGlobalProgresses(minuteLine, sessionStartMinute) {
  const rows = []
  minuteLine.forEach((point, index) => {
    const elapsed = sessionElapsed(clockMinuteOfDay(point.time), sessionStartMinute)
    if (elapsed != null) rows.push({ index, elapsed })
  })
  if (rows.length < 2) return new Map()
  let removed = 0
  let previous = rows[0].elapsed
  const compressed = rows.map((row, index) => {
    if (index > 0) {
      const gap = row.elapsed - previous
      if (gap > 30) removed += Math.max(0, gap - 1)
      previous = row.elapsed
    }
    return { index: row.index, elapsed: row.elapsed - removed }
  })
  const denominator = Math.max(1, 24 * 60 - removed)
  return new Map(compressed.map(row => [row.index, clamp01(row.elapsed / denominator)]))
}

function pointProgress(point, item, fallback, sessionStartMinute) {
  const marketType = String(item.market_type || '')
  if (marketType === 'a_index') {
    const explicitMinute = Number(point.minute)
    const minute = Number.isFinite(explicitMinute) ? explicitMinute : tradeMinuteOfDay(point.time)
    if (minute != null) return clamp01(minute / 240)
  }
  const clockMinute = clockMinuteOfDay(point.time)
  if (clockMinute != null) {
    if (marketType === 'us_index') return clamp01((clockMinute - (9 * 60 + 30)) / 390)
    const elapsed = sessionElapsed(clockMinute, sessionStartMinute)
    if (elapsed != null) return clamp01(elapsed / (24 * 60))
  }
  return fallback
}

const chart = computed(() => {
  const width = 120
  const height = 34
  const padding = 4
  const minuteLine = Array.isArray(props.item.minute_line) ? props.item.minute_line : []
  let points = []
  if (minuteLine.length >= 2) {
    const sessionStartMinute = minuteLine
      .map(point => clockMinuteOfDay(point.time))
      .find(minute => minute != null) ?? null
    const marketType = String(props.item.market_type || '')
    const compressed = marketType && !['a_index', 'us_index'].includes(marketType)
      ? compressedGlobalProgresses(minuteLine, sessionStartMinute)
      : new Map()
    points = minuteLine.map((point, index) => {
      const price = Number(point.price)
      if (!Number.isFinite(price) || price <= 0) return null
      const fallback = index / Math.max(1, minuteLine.length - 1)
      const progress = compressed.has(index)
        ? compressed.get(index)
        : pointProgress(point, props.item, fallback, sessionStartMinute)
      return { price, x: clamp01(progress) * width }
    }).filter(Boolean)
  } else {
    const prices = (props.item.sparkline || [])
      .map(value => Number(value))
      .filter(value => Number.isFinite(value) && value > 0)
    points = prices.map((price, index) => ({
      price,
      x: index / Math.max(1, prices.length - 1) * width,
    }))
  }
  if (points.length < 2) return null

  const currentPrice = Number(props.item.price)
  const currentChange = Number(props.item.change)
  const currentPct = Number(props.item.change_pct)
  let base = Number(props.item.prev_close ?? props.item.prevClose)
  if (!Number.isFinite(base) || base <= 0) {
    if (Number.isFinite(currentPrice) && Number.isFinite(currentChange) && Math.abs(currentPrice - currentChange) > 0) {
      base = currentPrice - currentChange
    } else if (Number.isFinite(currentPrice) && Number.isFinite(currentPct) && currentPct > -99.9) {
      base = currentPrice / (1 + currentPct / 100)
    }
  }
  if (!Number.isFinite(base) || base <= 0) base = points[0].price

  const percentages = points.map(point => (point.price / base - 1) * 100)
  const minimum = Math.min(0, ...percentages)
  const maximum = Math.max(0, ...percentages)
  const rangePadding = Math.max((maximum - minimum) * 0.16, 0.05)
  const yMin = minimum - rangePadding
  const yMax = maximum + rangePadding
  const span = yMax - yMin || 1
  const y = value => height - padding - (value - yMin) / span * (height - padding * 2)
  const coordinates = percentages.map((value, index) => [points[index].x, y(value)])
  const line = coordinates
    .map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(1)} ${point[1].toFixed(1)}`)
    .join(' ')
  const zeroY = y(0).toFixed(1)
  const firstX = coordinates[0][0].toFixed(1)
  const lastX = coordinates.at(-1)[0].toFixed(1)
  return {
    width,
    height,
    zeroY,
    line,
    area: `${line} L${lastX} ${zeroY} L${firstX} ${zeroY} Z`,
  }
})
</script>

<template>
  <svg
    v-if="chart"
    class="sparkline"
    :viewBox="`0 0 ${chart.width} ${chart.height}`"
    preserveAspectRatio="none"
  >
    <line
      class="sparkline-zero"
      x1="0"
      :x2="chart.width"
      :y1="chart.zeroY"
      :y2="chart.zeroY"
    >
      <title>0% 基准线</title>
    </line>
    <path class="sparkline-area" :d="chart.area" />
    <path class="sparkline-line" :d="chart.line" />
  </svg>
</template>
