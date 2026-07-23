<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { buildPracticeChartModel } from '../../utils/practiceChart.js'
import {
  formatPracticeAmount,
  formatPracticeNumber,
  signedPracticeAmount,
  signedPracticeNumber,
} from '../../utils/practiceDisplay.js'

const props = defineProps({ practice: { type: Object, required: true } })
const emit = defineEmits(['open-calendar'])
const initialParams = new URLSearchParams(location.search)
const mode = ref(initialParams.get('curve') === 'daily' ? 'daily' : 'intraday')
const hoverIndex = ref(-1)
const hoverActive = ref(false)
const chart = computed(() => buildPracticeChartModel(props.practice, mode.value))
const markerUp = computed(() => Number(chart.value.delta || 0) >= 0)
const markerColor = computed(() => markerUp.value ? '#ff4d4f' : '#39d98a')
const markerGlow = computed(() => markerUp.value ? 'rgba(255,77,79,.55)' : 'rgba(57,217,138,.55)')
const currentHover = computed(() => {
  const points = chart.value.points || []
  return points[Math.max(0, hoverIndex.value < 0 ? points.length - 1 : hoverIndex.value)] || null
})
const tradeMarkers = computed(() => (chart.value.trades || []).map(trade => {
  const targetX = trade.xPct / 100 * chart.value.width
  const nearest = (chart.value.points || []).reduce((best, point) => (
    !best || Math.abs(point.x - targetX) < Math.abs(best.x - targetX) ? point : best
  ), null)
  const sideClass = trade.action === 'BUY'
    ? 'buy'
    : trade.isFullExit ? 'sell-full' : 'sell-partial'
  const side = trade.action === 'BUY' ? '买' : '卖'
  const text = `${side} ${trade.name || trade.code || '--'} ${Number.isFinite(trade.shares) ? trade.shares : '--'}股×${Number.isFinite(trade.price) ? trade.price.toFixed(2) : '--'}`
  return {
    ...trade,
    side,
    sideClass,
    text,
    marker: trade.action === 'BUY' ? 'B' : 'S',
    yPct: nearest ? nearest.y / chart.value.height * 100 : 50,
  }
}))

function setMode(nextMode) {
  mode.value = nextMode === 'daily' ? 'daily' : 'intraday'
  hoverIndex.value = -1
  const next = new URL(location.href)
  if (mode.value === 'daily') next.searchParams.set('curve', 'daily')
  else next.searchParams.delete('curve')
  history.replaceState(null, '', `${next.pathname}${next.search}${next.hash}`)
}

function restoreFromUrl() {
  mode.value = new URLSearchParams(location.search).get('curve') === 'daily' ? 'daily' : 'intraday'
}

function updateHover(event) {
  const points = chart.value.points || []
  if (!points.length) return
  const rect = event.currentTarget.getBoundingClientRect()
  const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left)) / Math.max(1, rect.width) * chart.value.width
  let nearestIndex = 0
  let distance = Math.abs(points[0].x - x)
  points.forEach((point, index) => {
    const nextDistance = Math.abs(point.x - x)
    if (nextDistance < distance) {
      nearestIndex = index
      distance = nextDistance
    }
  })
  hoverIndex.value = nearestIndex
  hoverActive.value = true
}

function hoverRows(point) {
  if (!point) return []
  if (chart.value.dailyMode) {
    const points = chart.value.points || []
    const index = points.indexOf(point)
    const previous = index > 0 ? points[index - 1] : null
    const dayDelta = previous ? point.equity - previous.equity : point.equity - chart.value.baseEquity
    const dayPct = previous?.equity ? dayDelta / previous.equity * 100 : point.pct
    return [
      ['累计金额', signedPracticeAmount(point.delta), point.delta],
      ['累计收益率', signedPracticeNumber(point.pct), point.pct],
      ['当日金额', signedPracticeAmount(dayDelta), dayDelta],
      ['当日收益率', signedPracticeNumber(dayPct), dayPct],
    ]
  }
  return [
    ['收益金额', signedPracticeAmount(point.delta), point.delta],
    ['收益率', signedPracticeNumber(point.pct), point.pct],
    ['账户净值', formatPracticeAmount(point.equity), null],
  ]
}

onMounted(() => window.addEventListener('popstate', restoreFromUrl))
onBeforeUnmount(() => window.removeEventListener('popstate', restoreFromUrl))
</script>

<template>
  <div class="practice-chart-card">
    <div class="practice-chart-head">
      <div>
        <div class="practice-chart-title-row">
          <div class="practice-chart-title">
            <span class="practice-chart-title-text">{{ chart.title }}<template v-if="!chart.dailyMode && practice.trading_calendar?.is_trading_day === false && chart.targetDate">（{{ chart.targetDate }}）</template></span>
            <span class="practice-chart-title-measure" aria-hidden="true">今日收益曲线（{{ chart.targetDate || '0000-00-00' }}）</span>
          </div>
          <div class="practice-mode-control" aria-label="收益曲线模式">
            <button class="practice-mode-btn" :class="{ active: mode === 'intraday' }" type="button" @click="setMode('intraday')">当日收益</button>
            <button class="practice-mode-btn" :class="{ active: mode === 'daily' }" type="button" @click="setMode('daily')">累计收益</button>
          </div>
          <button class="practice-calendar-open-btn" type="button" @click="emit('open-calendar')">交易日历</button>
        </div>
        <div class="practice-chart-sub">
          <template v-if="chart.available && chart.dailyMode">按交易日最后净值计算 · 0轴为起始资金 · 最近点：{{ chart.lastPoint.time.slice(0, 10) }}</template>
          <template v-else-if="chart.available">固定盘面时间轴 09:30-15:00 · {{ chart.baseTime ? `0轴为上一交易日净值(${chart.baseTime.slice(5, 16)})` : '0轴为今日首个净值' }} · 最近点：{{ chart.lastPoint.time.slice(5, 16) }}</template>
          <template v-else>北京时间 {{ chart.targetDate || '--' }} · 等待{{ chart.dailyMode ? '交易日' : '今日盘中' }}净值点<template v-if="chart.latestDataDate && chart.latestDataDate !== chart.targetDate"> · 最近已有分时点 {{ chart.latestDataDate }}</template></template>
        </div>
        <div v-if="chart.available" class="benchmark-toggle-row"><button class="benchmark-toggle on" type="button" :style="`--dot:${markerColor}`"><span class="benchmark-dot"></span>牛牛账户收益率</button></div>
      </div>
      <div v-if="chart.available" class="practice-chart-kpis">
        <div class="practice-kpi"><div class="practice-kpi-label">{{ chart.dailyMode ? '最新总收益' : '当日收益' }}</div><div class="practice-kpi-value" :class="chart.delta >= 0 ? 'up' : 'down'">{{ signedPracticeAmount(chart.delta) }} / {{ signedPracticeNumber(chart.deltaPct) }}</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">{{ chart.dailyMode ? '较前日变化' : '累计收益' }}</div><div class="practice-kpi-value" :class="(chart.dailyMode ? chart.dayDelta : chart.totalPnl) >= 0 ? 'up' : 'down'">{{ signedPracticeAmount(chart.dailyMode ? chart.dayDelta : chart.totalPnl) }} / {{ signedPracticeNumber(chart.dailyMode ? chart.dayPct : chart.totalPct) }}</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">最大回撤</div><div class="practice-kpi-value down">{{ formatPracticeNumber(chart.maxDrawdown) }}%</div></div>
      </div>
    </div>
    <div v-if="!chart.available" class="empty" style="padding:18px">{{ chart.dailyMode ? '累计收益等待更多交易日净值点…' : `今日收益曲线等待北京时间 ${chart.targetDate || '--'} 的盘中净值点…` }}</div>
    <div
      v-else
      class="practice-chart-wrap"
      @pointerenter="updateHover"
      @pointermove="updateHover"
      @pointerleave="hoverActive = false"
    >
      <span
        v-for="tick in chart.yTicks"
        :key="`axis-${tick.value}`"
        class="practice-axis-label"
        :class="{ zero: tick.isZero }"
        :style="`top:${tick.y / chart.height * 100}%`"
      >{{ formatPracticeNumber(tick.value, chart.bounds.digits) }}%</span>
      <svg class="practice-chart-svg" :viewBox="`0 0 ${chart.width} ${chart.height}`" preserveAspectRatio="none">
        <defs>
          <linearGradient id="practiceFillVue" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" :stop-color="markerColor" stop-opacity="0.30"/><stop offset="100%" :stop-color="markerColor" stop-opacity="0.02"/></linearGradient>
        </defs>
        <line
          v-for="tick in chart.yTicks"
          :key="`grid-${tick.value}`"
          :x1="chart.left"
          :x2="chart.width-chart.right"
          :y1="tick.y"
          :y2="tick.y"
          :stroke="tick.isZero ? 'var(--chart-zero)' : 'var(--chart-grid)'"
          :stroke-width="tick.isZero ? 1.2 : 1"
          :stroke-dasharray="tick.isZero ? '7 5' : '4 6'"
        />
        <line v-for="tick in chart.timeTicks" :key="`${tick.label}-${tick.x}`" :x1="tick.x" :x2="tick.x" :y1="chart.top" :y2="chart.height-chart.bottom" stroke="var(--chart-grid-soft)" />
        <path :d="chart.area" fill="url(#practiceFillVue)" />
        <path :d="chart.line" fill="none" :stroke="markerColor" stroke-width="2.2" vector-effect="non-scaling-stroke" />
      </svg>
      <span class="practice-current-line" :style="`left:${chart.lastPoint.x / chart.width * 100}%`"></span>
      <span class="practice-current-marker" :style="`left:${chart.lastPoint.x / chart.width * 100}%;top:${chart.lastPoint.y / chart.height * 100}%;--marker-color:${markerColor};--marker-glow:${markerGlow}`" :title="`当前 ${formatPracticeAmount(chart.lastPoint.equity)}`"></span>
      <span
        v-if="currentHover"
        class="practice-chart-hover-layer"
        :class="[{ active: hoverActive, 'place-left': currentHover.x / chart.width > .66, 'place-bottom': currentHover.y / chart.height < .34 }]"
        :style="`--hover-x-pct:${currentHover.x / chart.width * 100}%;--hover-y-pct:${currentHover.y / chart.height * 100}%;--marker-color:${markerColor};--marker-glow:${markerGlow}`"
      >
        <span class="practice-hover-line"></span><span class="practice-hover-marker"></span>
        <span class="practice-hover-tooltip"><span class="practice-hover-tooltip-time">{{ chart.dailyMode ? currentHover.time.slice(0, 10) : currentHover.time.slice(5, 16) }}</span><span v-for="row in hoverRows(currentHover)" :key="row[0]" class="practice-hover-tooltip-row"><span>{{ row[0] }}</span><strong :class="row[2] == null ? '' : row[2] >= 0 ? 'up' : 'down'">{{ row[1] }}</strong></span></span>
      </span>
      <button
        v-for="trade in tradeMarkers"
        :key="`${trade.time}-${trade.action}-${trade.code}`"
        type="button"
        class="practice-trade-marker"
        :class="trade.sideClass"
        :style="`--marker-x:${trade.xPct}%;top:${trade.yPct}%`"
        :aria-label="trade.text"
      >{{ trade.marker }}<span class="practice-trade-marker-tooltip" aria-hidden="true"><span class="practice-trade-marker-time">{{ trade.time.slice(11, 16) }}</span><span class="practice-trade-marker-line" :class="trade.action === 'BUY' ? 'buy' : 'sell'"><span class="practice-trade-marker-side">{{ trade.side }}</span><span class="practice-trade-marker-stock">{{ trade.name || trade.code }}</span><span class="practice-trade-marker-fill">{{ Number.isFinite(trade.shares) ? trade.shares : '--' }}股×{{ Number.isFinite(trade.price) ? trade.price.toFixed(2) : '--' }}</span><span v-if="trade.action === 'SELL' && trade.isFullExit && Number.isFinite(trade.pnl)" class="practice-trade-marker-pnl" :class="trade.pnl >= 0 ? 'up' : 'down'">盈亏{{ signedPracticeAmount(trade.pnl) }}<template v-if="Number.isFinite(trade.pnlPct)"> ({{ signedPracticeNumber(trade.pnlPct) }})</template></span></span></span></button>
      <span v-for="(tick, index) in chart.timeTicks" :key="`label-${tick.label}-${tick.x}`" class="practice-time-label" :class="index === 0 ? 'start' : index === chart.timeTicks.length - 1 ? 'end' : 'mid'" :style="`left:${tick.x / chart.width * 100}%`">{{ tick.label }}</span>
    </div>
  </div>
</template>
