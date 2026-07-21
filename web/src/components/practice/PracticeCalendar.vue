<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  buildPracticeCalendarRows,
  clampedTradingClockMinuteOfDay,
  compactPracticeDailyPoints,
  currentChinaDateKey,
  normalizePracticeEquityPoints,
  normalizePracticeTradeMarkers,
  practiceCalendarHistoryCoversDate,
  practiceCalendarHistoryPoints,
  tradingClockMinuteOfDay,
} from '../../utils/practiceChart.js'
import {
  formatPracticeAmount,
  formatPracticeNumber,
  signedPracticeAmount,
  signedPracticeNumber,
} from '../../utils/practiceDisplay.js'

const props = defineProps({
  open: Boolean,
  practice: { type: Object, required: true },
  fullSnapshotStatus: { type: String, default: 'idle' },
})
const emit = defineEmits(['close', 'ensure-full'])
const month = ref('')
const selectedDate = ref('')

const initialCash = computed(() => Number(props.practice.initial_cash || 1_000_000))
const history = computed(() => practiceCalendarHistoryPoints(props.practice))
const dailyHistory = computed(() => normalizePracticeEquityPoints(props.practice.daily_equity_history || []))
const rows = computed(() => buildPracticeCalendarRows(history.value, dailyHistory.value, initialCash.value))
const latestMonth = computed(() => rows.value.at(-1)?.date.slice(0, 7) || currentChinaDateKey().slice(0, 7))
const monthParts = computed(() => {
  const match = String(month.value || latestMonth.value).match(/^(\d{4})-(\d{2})$/)
  return match
    ? { year: Number(match[1]), month: Number(match[2]) }
    : { year: new Date().getFullYear(), month: new Date().getMonth() + 1 }
})
const rowByDate = computed(() => new Map(rows.value.map(row => [row.date, row])))
const monthRows = computed(() => rows.value.filter(row => row.date.startsWith(month.value)))
const monthPnl = computed(() => monthRows.value.reduce((sum, row) => sum + Number(row.pnl || 0), 0))
const monthBase = computed(() => monthRows.value.length
  ? monthRows.value[0].equity - monthRows.value[0].pnl
  : initialCash.value)
const monthPct = computed(() => monthBase.value ? monthPnl.value / monthBase.value * 100 : 0)
const winDays = computed(() => monthRows.value.filter(row => Number(row.pnl) > 0).length)
const lossDays = computed(() => monthRows.value.filter(row => Number(row.pnl) < 0).length)
const flatDays = computed(() => Math.max(0, monthRows.value.length - winDays.value - lossDays.value))
const cells = computed(() => {
  const { year, month: monthNumber } = monthParts.value
  const firstWeekday = (new Date(year, monthNumber - 1, 1).getDay() + 6) % 7
  const daysInMonth = new Date(year, monthNumber, 0).getDate()
  const result = Array.from({ length: firstWeekday }, (_, index) => ({ blank: true, key: `blank-${index}` }))
  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = `${month.value}-${String(day).padStart(2, '0')}`
    const row = rowByDate.value.get(date) || null
    const dayOfWeek = new Date(year, monthNumber - 1, day).getDay()
    result.push({
      key: date,
      date,
      day,
      row,
      weekend: dayOfWeek === 0 || dayOfWeek === 6,
      today: date === currentChinaDateKey(),
    })
  }
  return result
})

const curve = computed(() => {
  const date = selectedDate.value
  if (!date) return null
  const allDayPoints = history.value.filter(point => point.time.slice(0, 10) === date)
  const sessionPoints = allDayPoints.filter(point => tradingClockMinuteOfDay(point.time) != null)
  const allDaily = compactPracticeDailyPoints([...history.value, ...dailyHistory.value])
  const previous = allDaily.filter(point => point.time.slice(0, 10) < date).at(-1)
  const baseEquity = Number(previous?.equity || initialCash.value)
  const row = rowByDate.value.get(date)
  const dailyPoint = dailyHistory.value.filter(point => point.time.slice(0, 10) === date).at(-1)
  const latestEquity = Number(sessionPoints.at(-1)?.equity ?? allDayPoints.at(-1)?.equity ?? dailyPoint?.equity ?? row?.equity)
  const pnl = Number.isFinite(latestEquity) ? latestEquity - baseEquity : Number(row?.pnl || 0)
  const pct = baseEquity ? pnl / baseEquity * 100 : Number(row?.pnlPct || 0)
  const currentDate = date === String(props.practice.current_date || '')
  const needsFull = currentDate || (
    String(props.practice.equity_history_scope || '') !== 'retained_history'
    && !practiceCalendarHistoryCoversDate(props.practice, date)
  )
  const loading = sessionPoints.length < 2 && needsFull && props.fullSnapshotStatus === 'loading'
  const failed = sessionPoints.length < 2 && needsFull && props.fullSnapshotStatus === 'error'
  let source = sessionPoints
  if (source.length < 2 && Number.isFinite(latestEquity) && baseEquity > 0) {
    source = [
      { time: `${date} 09:30:00`, equity: baseEquity },
      { time: `${date} 15:00:00`, equity: latestEquity },
    ]
  }
  if (source.length < 2) return { date, pnl, pct, previous, loading, failed, available: false }
  const width = 464
  const height = 96
  const left = 8
  const right = 12
  const top = 8
  const bottom = 14
  const innerWidth = width - left - right
  const innerHeight = height - top - bottom
  const points = source.map(point => ({
    ...point,
    minute: clampedTradingClockMinuteOfDay(point.time),
    pct: (Number(point.equity) - baseEquity) / baseEquity * 100,
  }))
  let min = Math.min(0, ...points.map(point => point.pct))
  let max = Math.max(0, ...points.map(point => point.pct))
  const pad = Math.max((max - min) * 0.12, 0.08)
  min -= pad
  max += pad
  const xFor = minute => left + minute / 240 * innerWidth
  const yFor = value => top + (max - value) / Math.max(0.0001, max - min) * innerHeight
  const plotted = points.map(point => ({ ...point, x: xFor(point.minute), y: yFor(point.pct) }))
  const path = plotted.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
  const last = plotted.at(-1)
  const area = `${path} L${last.x.toFixed(1)},${height - bottom} L${plotted[0].x.toFixed(1)},${height - bottom} Z`
  const trades = normalizePracticeTradeMarkers(props.practice).filter(trade => trade.time.slice(0, 10) === date)
  return {
    date, pnl, pct, previous, loading, failed, available: true,
    width, height, left, right, top, bottom, innerWidth, path, area, plotted,
    last, zeroY: yFor(0), trades,
  }
})

function valueClass(value) {
  return Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : 'flat'
}

function shiftMonth(delta) {
  const { year, month: monthNumber } = monthParts.value
  const next = new Date(year, monthNumber - 1 + delta, 1)
  month.value = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`
  selectedDate.value = ''
}

function selectDate(date) {
  selectedDate.value = selectedDate.value === date ? '' : date
  if (!selectedDate.value) return
  const hasSession = history.value.filter(point => point.time.slice(0, 10) === date && tradingClockMinuteOfDay(point.time) != null).length >= 2
  if (!hasSession && !practiceCalendarHistoryCoversDate(props.practice, date)) emit('ensure-full')
}

function close() {
  selectedDate.value = ''
  emit('close')
}

function handleKeydown(event) {
  if (props.open && event.key === 'Escape') close()
}

watch(() => props.open, open => {
  if (open) {
    month.value = latestMonth.value
    selectedDate.value = ''
  }
})
onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="practice-calendar-popover" @click.self="close">
      <div v-if="curve" class="practice-calendar-day-curve" data-practice-calendar-curve>
        <div class="practice-calendar-day-curve-head">
          <div><div class="practice-calendar-day-curve-title">{{ curve.date.slice(5) }} 当日收益曲线</div><div class="practice-calendar-day-curve-sub">{{ curve.available && curve.plotted.length > 2 ? '' : curve.loading ? '分时加载中 · ' : curve.failed ? '分时加载失败 · ' : '仅有收盘点 · ' }}0轴 {{ curve.previous ? curve.previous.time.slice(5, 16) : '初始资金' }}</div></div>
          <div class="practice-calendar-day-curve-value" :class="valueClass(curve.pnl)">{{ signedPracticeAmount(curve.pnl) }} / {{ signedPracticeNumber(curve.pct) }}</div>
          <button type="button" class="practice-calendar-day-curve-close" title="关闭曲线" aria-label="关闭曲线" @click="selectedDate = ''">x</button>
        </div>
        <div v-if="curve.loading" class="practice-calendar-day-curve-empty" aria-live="polite">分时曲线加载中…</div>
        <div v-else-if="curve.failed" class="practice-calendar-day-curve-empty" role="status">分时曲线加载失败</div>
        <div v-else-if="!curve.available" class="practice-calendar-day-curve-empty">等待当日分时点</div>
        <div v-else class="practice-calendar-day-curve-chart">
          <svg class="practice-calendar-day-curve-svg" :viewBox="`0 0 ${curve.width} ${curve.height}`" role="img" :aria-label="`${curve.date} 当日收益曲线`">
            <line :x1="curve.left" :y1="curve.zeroY" :x2="curve.width-curve.right" :y2="curve.zeroY" stroke="var(--chart-zero)" stroke-width="1" stroke-dasharray="4 5" />
            <path :d="curve.area" :fill="curve.pnl >= 0 ? 'rgba(255,77,79,.13)' : 'rgba(57,217,138,.13)'" />
            <path :d="curve.path" fill="none" :stroke="curve.pnl >= 0 ? '#ff4d4f' : '#39d98a'" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
            <circle :cx="curve.last.x" :cy="curve.last.y" r="4" fill="#f8fafc" :stroke="curve.pnl >= 0 ? '#ff4d4f' : '#39d98a'" stroke-width="2" />
            <text :x="curve.left" :y="curve.height-2" fill="#7b8aa0" font-size="9">09:30</text><text :x="curve.left+curve.innerWidth/2" :y="curve.height-2" fill="#7b8aa0" font-size="9" text-anchor="middle">11:30</text><text :x="curve.width-curve.right" :y="curve.height-2" fill="#7b8aa0" font-size="9" text-anchor="end">15:00</text>
          </svg>
        </div>
      </div>
      <div class="practice-calendar-card" role="dialog" aria-label="交易日历">
        <div class="practice-calendar-head">
          <div><div class="practice-calendar-title">交易日历 · {{ monthParts.year }}年{{ String(monthParts.month).padStart(2, '0') }}月</div><div class="practice-calendar-sub">{{ monthRows.length ? `有记录 ${monthRows.length} 天 · 最近 ${monthRows.at(-1).date}` : '本月暂无收益记录' }}</div></div>
          <div class="practice-calendar-actions"><button type="button" class="practice-calendar-icon-btn" title="上个月" aria-label="上个月" @click="shiftMonth(-1)">‹</button><button type="button" class="practice-calendar-icon-btn" title="下个月" aria-label="下个月" @click="shiftMonth(1)">›</button><button type="button" class="practice-calendar-icon-btn" title="关闭" aria-label="关闭" @click="close">x</button></div>
        </div>
        <div class="practice-calendar-summary">
          <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">本月收益</div><div class="practice-calendar-stat-value" :class="valueClass(monthPnl)">{{ signedPracticeAmount(monthPnl) }} / {{ signedPracticeNumber(monthPct) }}</div></div>
          <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">盈利天数</div><div class="practice-calendar-stat-value up">{{ winDays }}</div></div>
          <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">亏损/持平</div><div class="practice-calendar-stat-value">{{ lossDays }} / {{ flatDays }}</div></div>
        </div>
        <div class="practice-calendar-grid-wrap">
          <div class="practice-calendar-weekdays"><div v-for="(day, index) in ['一','二','三','四','五','六','日']" :key="day" class="practice-calendar-weekday" :class="{ weekend: index >= 5 }">{{ day }}</div></div>
          <div class="practice-calendar-grid">
            <div v-for="cell in cells" :key="cell.key" class="practice-calendar-day" :class="cell.blank ? 'blank' : [{ weekend: cell.weekend && !cell.row, selected: cell.date === selectedDate, 'has-result': cell.row }, cell.row ? valueClass(cell.row.pnl) : '']" :aria-hidden="cell.blank ? 'true' : undefined" :aria-label="cell.row ? `${cell.date} ${signedPracticeNumber(cell.row.pnlPct)} / ${signedPracticeAmount(cell.row.pnl)}` : cell.date" @click="cell.row && selectDate(cell.date)">
              <template v-if="!cell.blank"><div class="practice-calendar-date"><span>{{ cell.day }}</span><span v-if="cell.today && (!cell.weekend || cell.row)" class="practice-calendar-today">今</span></div><div v-if="cell.row" class="practice-calendar-values"><div class="practice-calendar-rate" :class="valueClass(cell.row.pnl)">{{ signedPracticeNumber(cell.row.pnlPct) }}</div><div class="practice-calendar-amount" :class="valueClass(cell.row.pnl)">{{ signedPracticeAmount(cell.row.pnl) }}</div></div><div v-else class="practice-calendar-no-data">--</div><span v-if="cell.today && cell.weekend && !cell.row" class="practice-calendar-today weekend-today">今</span></template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
