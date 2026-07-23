import { mergePracticeTimedRows } from './practicePayload.js'

export function currentChinaDateKey(date = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(date)
    const get = type => parts.find(part => part.type === type)?.value || ''
    if (get('year') && get('month') && get('day')) return `${get('year')}-${get('month')}-${get('day')}`
  } catch {}
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function tradingClockMinuteOfDay(timeText) {
  const match = String(timeText || '').match(/(\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!match) return null
  const minute = Number(match[1]) * 60 + Number(match[2]) + Number(match[3] || 0) / 60
  const start = 9 * 60 + 30
  const morningEnd = 11 * 60 + 30
  const afternoonStart = 13 * 60
  const end = 15 * 60
  if (minute < start || minute > end || (minute > morningEnd && minute < afternoonStart)) return null
  return minute <= morningEnd ? minute - start : minute - start - 90
}

export function clampedTradingClockMinuteOfDay(timeText) {
  const match = String(timeText || '').match(/(\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!match) return 0
  const minute = Number(match[1]) * 60 + Number(match[2]) + Number(match[3] || 0) / 60
  const start = 9 * 60 + 30
  const morningEnd = 11 * 60 + 30
  const afternoonStart = 13 * 60
  const end = 15 * 60
  if (minute <= start) return 0
  if (minute <= morningEnd) return minute - start
  if (minute < afternoonStart) return 120
  if (minute <= end) return minute - start - 90
  return 240
}

export function normalizePracticeEquityPoints(source) {
  return (Array.isArray(source) ? source : [])
    .map(point => ({
      time: String(point?.time || ''),
      equity: Number(point?.equity),
      pnlPct: Number(point?.pnl_pct ?? point?.pnlPct),
    }))
    .filter(point => point.time && Number.isFinite(point.equity))
}

export function compactPracticeDailyPoints(points) {
  const byDate = new Map()
  for (const point of points || []) {
    const date = String(point?.time || '').slice(0, 10)
    if (!date) continue
    const previous = byDate.get(date)
    if (!previous || String(point.time) >= String(previous.time)) byDate.set(date, point)
  }
  return [...byDate.values()].sort((left, right) => left.time.localeCompare(right.time))
}

export function compactPracticeCalendarHistoryPoints(payload) {
  const calendar = payload?.calendar_history
  if (!calendar || Number(calendar.schema_version) !== 1 || !calendar.days || typeof calendar.days !== 'object') return []
  const points = []
  for (const [date, rows] of Object.entries(calendar.days)) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Array.isArray(rows)) continue
    for (const row of rows) {
      const clock = String(row?.clock || '')
      if (!/^\d{2}:\d{2}(?::\d{2})?$/.test(clock)) continue
      points.push({ time: `${date} ${clock}`, equity: Number(row?.equity) })
    }
  }
  return points.filter(point => Number.isFinite(point.equity))
}

export function practiceCalendarHistoryPoints(payload) {
  const rawPoints = normalizePracticeEquityPoints(payload?.equity_history || [])
  const rawDates = new Set(rawPoints.map(point => point.time.slice(0, 10)))
  const compactPoints = compactPracticeCalendarHistoryPoints(payload)
    .filter(point => !rawDates.has(point.time.slice(0, 10)))
  return normalizePracticeEquityPoints(mergePracticeTimedRows(compactPoints, rawPoints))
}

export function practiceCalendarHistoryCoversDate(payload, date) {
  const calendar = payload?.calendar_history
  if (!calendar || Number(calendar.schema_version) !== 1 || calendar.complete !== true || !calendar.days) return false
  if (Object.hasOwn(calendar.days, date)) return true
  const start = String(calendar.coverage_start || '')
  const end = String(calendar.coverage_end || '')
  return Boolean(start && end && date >= start && date <= end)
}

export function buildPracticeCalendarRows(history, dailyHistory, initialCash = 1_000_000) {
  const byDate = new Map()
  const points = [
    ...compactPracticeDailyPoints(normalizePracticeEquityPoints(history)),
    ...compactPracticeDailyPoints(normalizePracticeEquityPoints(dailyHistory)),
  ]
  for (const point of points) {
    const date = point.time.slice(0, 10)
    const previous = byDate.get(date)
    if (!previous || point.time >= previous.time) byDate.set(date, point)
  }
  let previousEquity = Number(initialCash)
  return [...byDate.values()].sort((left, right) => left.time.localeCompare(right.time)).map(point => {
    const equity = Number(point.equity)
    const base = Number.isFinite(previousEquity) && previousEquity > 0 ? previousEquity : equity
    const pnl = Number.isFinite(equity) && Number.isFinite(base) ? equity - base : 0
    const pnlPct = base ? pnl / base * 100 : 0
    previousEquity = equity
    return { date: point.time.slice(0, 10), time: point.time, equity, pnl, pnlPct }
  })
}

export function normalizePracticeTradeMarkers(payload) {
  const source = Array.isArray(payload?.trade_markers) && payload.trade_markers.length
    ? payload.trade_markers
    : (payload?.trade_log || [])
  return (Array.isArray(source) ? source : []).map(raw => {
    const action = String(raw?.action || '').toUpperCase()
    const afterPct = raw?.position_after_trade_pct
    return {
      time: String(raw?.time || ''),
      action,
      code: String(raw?.code || ''),
      name: String(raw?.name || ''),
      shares: Number(raw?.shares),
      price: Number(raw?.price),
      pnl: Number(raw?.pnl),
      pnlPct: Number(raw?.pnl_pct),
      isFullExit: raw?.is_full_exit === true
        || (action === 'SELL' && afterPct != null && Number(afterPct) <= 0),
    }
  }).filter(trade => trade.time && ['BUY', 'SELL'].includes(trade.action))
    .sort((left, right) => left.time.localeCompare(right.time))
}

function axisBounds(values, clampOneSidedAtZero = false) {
  const finite = values.map(Number).filter(Number.isFinite)
  if (!finite.length) return { min: -0.01, max: 0.01, digits: 3 }
  const dataMin = Math.min(...finite)
  const dataMax = Math.max(...finite)
  const range = Math.max(0, dataMax - dataMin)
  const minimumSpan = Math.min(0.2, Math.max(0.02, range * 1.4))
  const pad = Math.max(range * 0.18, minimumSpan * 0.1)
  let min = dataMin - pad
  let max = dataMax + pad
  if (max - min < minimumSpan) {
    const expand = (minimumSpan - (max - min)) / 2
    min -= expand
    max += expand
  }
  if (dataMin <= 0 && dataMax >= 0 || Math.min(Math.abs(dataMin), Math.abs(dataMax)) <= Math.max(range * 2, 0.04)) {
    min = Math.min(min, -0.01)
    max = Math.max(max, 0.01)
  }
  if (clampOneSidedAtZero) {
    if (dataMax > 0 && dataMin >= 0) min = Math.max(0, min)
    else if (dataMin < 0 && dataMax <= 0) max = Math.min(0, max)
  }
  return { min, max, digits: max - min < 0.05 ? 3 : 2 }
}

function axisTicks(bounds, yFor) {
  const span = Math.max(0.0001, bounds.max - bounds.min)
  const tolerance = Math.max(1e-9, span * 1e-9)
  const minIsZero = Math.abs(bounds.min) <= tolerance
  const maxIsZero = Math.abs(bounds.max) <= tolerance
  let values
  if (minIsZero && bounds.max > 0) values = [bounds.max, bounds.max / 2, 0]
  else if (maxIsZero && bounds.min < 0) values = [0, bounds.min / 2, bounds.min]
  else if (bounds.min < 0 && bounds.max > 0) values = [bounds.max, 0, bounds.min]
  else values = [bounds.max, (bounds.max + bounds.min) / 2, bounds.min]

  const unique = []
  for (const rawValue of values) {
    const value = Math.abs(rawValue) <= tolerance ? 0 : rawValue
    if (!unique.some(existing => Math.abs(existing - value) <= tolerance)) unique.push(value)
  }
  return unique.map(value => ({ value, y: yFor(value), isZero: value === 0 }))
}

function straightPath(points) {
  return points.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' ')
}

export function buildPracticeChartModel(payload, mode = 'intraday') {
  const dailyMode = mode === 'daily'
  const initialCash = Number(payload?.initial_cash || 1_000_000)
  const intraday = normalizePracticeEquityPoints(payload?.equity_history || [])
  const daily = compactPracticeDailyPoints([
    ...intraday,
    ...normalizePracticeEquityPoints(payload?.daily_equity_history || []),
  ])
  const payloadDate = String(payload?.current_date || payload?.trading_calendar?.date || currentChinaDateKey()).slice(0, 10)
  const latestDataDate = intraday.at(-1)?.time.slice(0, 10) || daily.at(-1)?.time.slice(0, 10) || ''
  const targetDate = payload?.trading_calendar?.is_trading_day === false ? latestDataDate : payloadDate
  let points = dailyMode
    ? daily
    : intraday.filter(point => point.time.slice(0, 10) === targetDate && tradingClockMinuteOfDay(point.time) != null)
  points = mergePracticeTimedRows(points).map(point => ({ ...point, equity: Number(point.equity) }))
  const minimumPoints = dailyMode ? 2 : 1
  if (points.length < minimumPoints) {
    return {
      available: false,
      dailyMode,
      targetDate,
      latestDataDate,
      title: dailyMode ? '累积收益曲线' : '今日收益曲线',
    }
  }
  const previousPoint = daily.filter(point => point.time.slice(0, 10) < targetDate).at(-1)
  const baseEquity = dailyMode
    ? initialCash
    : (Number(previousPoint?.equity) > 0 ? Number(previousPoint.equity) : Number(points[0].equity))
  const width = 720
  const height = 210
  const left = 12
  const right = 58
  const top = 18
  const bottom = 24
  const innerWidth = width - left - right
  const innerHeight = height - top - bottom
  const values = points.map(point => Number(point.equity))
  const percentages = values.map(value => baseEquity ? (value / baseEquity - 1) * 100 : 0)
  const bounds = axisBounds(dailyMode ? percentages : [0, ...percentages], !dailyMode)
  const yFor = value => top + (bounds.max - value) / Math.max(0.0001, bounds.max - bounds.min) * innerHeight
  const yTicks = axisTicks(bounds, yFor)
  const xFor = (point, index) => dailyMode
    ? left + index / Math.max(1, points.length - 1) * innerWidth
    : left + clampedTradingClockMinuteOfDay(point.time) / 240 * innerWidth
  const plotted = points.map((point, index) => ({
    ...point,
    pct: percentages[index],
    delta: Number(point.equity) - baseEquity,
    x: xFor(point, index),
    y: yFor(percentages[index]),
  }))
  if (!dailyMode && previousPoint && plotted[0]?.x > left) {
    plotted.unshift({
      time: `${targetDate} 09:30:00`,
      equity: baseEquity,
      pct: 0,
      delta: 0,
      x: left,
      y: yFor(0),
      synthetic: true,
    })
  }
  const line = straightPath(plotted)
  const zeroY = yFor(Math.max(bounds.min, Math.min(bounds.max, 0)))
  const area = `${line} L${plotted.at(-1).x.toFixed(1)} ${zeroY.toFixed(1)} L${plotted[0].x.toFixed(1)} ${zeroY.toFixed(1)} Z`
  const lastEquity = values.at(-1)
  const delta = lastEquity - baseEquity
  const deltaPct = baseEquity ? delta / baseEquity * 100 : 0
  const totalPnl = lastEquity - initialCash
  const totalPct = initialCash ? totalPnl / initialCash * 100 : 0
  const previousEquity = values.at(-2) ?? baseEquity
  const dayDelta = lastEquity - previousEquity
  const dayPct = previousEquity ? dayDelta / previousEquity * 100 : 0
  let peak = baseEquity
  let maxDrawdown = 0
  for (const value of values) {
    peak = Math.max(peak, value)
    maxDrawdown = Math.min(maxDrawdown, peak ? (value / peak - 1) * 100 : 0)
  }
  const trades = dailyMode ? [] : normalizePracticeTradeMarkers(payload)
    .filter(trade => trade.time.slice(0, 10) === targetDate && tradingClockMinuteOfDay(trade.time) != null)
    .map(trade => ({ ...trade, xPct: (left + clampedTradingClockMinuteOfDay(trade.time) / 240 * innerWidth) / width * 100 }))
  return {
    available: true,
    dailyMode,
    title: dailyMode ? '累积收益曲线' : '今日收益曲线',
    targetDate,
    latestDataDate,
    baseEquity,
    baseTime: previousPoint?.time || '',
    width,
    height,
    left,
    right,
    top,
    bottom,
    bounds,
    yTicks,
    zeroY,
    line,
    area,
    points: plotted,
    lastPoint: plotted.at(-1),
    delta,
    deltaPct,
    totalPnl,
    totalPct,
    dayDelta,
    dayPct,
    maxDrawdown,
    trades,
    timeTicks: dailyMode
      ? [plotted[0], plotted[Math.floor((plotted.length - 1) / 2)], plotted.at(-1)].map(point => ({ label: point.time.slice(5, 10), x: point.x }))
      : [{ label: '09:30', x: left }, { label: '11:30', x: left + innerWidth / 2 }, { label: '15:00', x: left + innerWidth }],
  }
}
