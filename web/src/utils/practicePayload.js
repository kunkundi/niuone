export function mergePracticeTimedRows(...sources) {
  const byTime = new Map()
  for (const source of sources) {
    for (const row of (Array.isArray(source) ? source : [])) {
      const time = String(row?.time || '')
      if (time) byTime.set(time, row)
    }
  }
  return [...byTime.values()].sort((left, right) => (
    String(left?.time || '').localeCompare(String(right?.time || ''))
  ))
}

function practicePayloadModeRank(payload) {
  const mode = String(payload?.snapshot_mode || '')
  if (mode === 'full') return 2
  if (mode === 'merged') {
    return String(payload?.equity_history_scope || '') === 'retained_history' ? 2 : 1
  }
  return mode === 'fast' ? 1 : 0
}

function practicePayloadFreshnessTuple(payload) {
  const meta = payload?.snapshot_meta || {}
  const latestEquityTime = mergePracticeTimedRows(payload?.equity_history || []).at(-1)?.time || ''
  const newest = values => values.map(value => String(value || '')).filter(Boolean).sort().at(-1) || ''
  const sourceUpdatedAt = newest([meta.source_updated_at, payload?.source_updated_at])
  const sourceLastEquity = newest([
    meta.source_last_equity_time,
    payload?.source_last_equity_time,
    latestEquityTime,
  ])
  const responseTime = newest([payload?.current_time, payload?.generated_at])
  return [
    sourceUpdatedAt || sourceLastEquity || responseTime,
    sourceLastEquity,
    practicePayloadModeRank(payload),
    responseTime,
  ]
}

export function comparePracticePayloadFreshness(left, right) {
  const leftTuple = practicePayloadFreshnessTuple(left)
  const rightTuple = practicePayloadFreshnessTuple(right)
  for (let index = 0; index < leftTuple.length; index += 1) {
    if (leftTuple[index] === rightTuple[index]) continue
    return leftTuple[index] > rightTuple[index] ? 1 : -1
  }
  return 0
}

export function isUsablePracticePayload(payload) {
  if (
    !payload
    || typeof payload !== 'object'
    || String(payload.equity_history_scope || '') === 'unavailable'
  ) return false
  const isLegacyErrorShell = !('equity_history_scope' in payload)
    && Boolean(payload.last_error)
    && (!Array.isArray(payload.positions) || payload.positions.length === 0)
    && (!Array.isArray(payload.equity_history) || payload.equity_history.length === 0)
    && ['cash', 'total_equity', 'initial_cash'].every(key => Number(payload[key] || 0) === 0)
  if (isLegacyErrorShell) return false
  const hasFiniteField = key => (
    payload[key] !== null
    && payload[key] !== ''
    && Number.isFinite(Number(payload[key]))
  )
  return Boolean(
    hasFiniteField('total_equity')
    || hasFiniteField('cash')
    || (Array.isArray(payload.positions) && ('initial_cash' in payload || 'cash' in payload))
    || (Array.isArray(payload.equity_history) && payload.equity_history.length)
  )
}

function mergePracticeDailyRows(staleRows, liveRows) {
  const byDate = new Map()
  for (const source of [staleRows, liveRows]) {
    for (const row of (Array.isArray(source) ? source : [])) {
      const date = String(row?.time || '').slice(0, 10)
      if (date) byDate.set(date, row)
    }
  }
  return [...byDate.values()].sort((left, right) => (
    String(left?.time || '').localeCompare(String(right?.time || ''))
  ))
}

function mergePracticeEquityRows(live, stale) {
  const liveRows = mergePracticeTimedRows(live?.equity_history || [])
  if (String(live?.equity_history_scope || '') === 'retained_history') return liveRows.slice(-2000)
  if (!liveRows.length) return []
  const liveDate = String(liveRows.at(-1)?.time || '').slice(0, 10)
  const compactDates = new Set(Object.keys(live?.calendar_history?.days || {}))
  const olderRows = mergePracticeTimedRows(stale?.equity_history || []).filter(row => {
    const date = String(row?.time || '').slice(0, 10)
    return date && liveDate && date < liveDate && !compactDates.has(date)
  })
  return mergePracticeTimedRows(olderRows, liveRows).slice(-2000)
}

export function mergePracticePayloadSnapshots(current, incoming) {
  if (!isUsablePracticePayload(current)) return { ...(incoming || {}) }
  if (!isUsablePracticePayload(incoming)) return { ...(current || {}) }
  const incomingIsFresher = comparePracticePayloadFreshness(incoming, current) >= 0
  const live = incomingIsFresher ? incoming : current
  const other = incomingIsFresher ? current : incoming
  const merged = { ...other, ...live }
  for (const key of ['decision_model', 'decision_provider']) {
    const incomingValue = String(incoming?.[key] || '').trim()
    if (incomingValue) merged[key] = incomingValue
  }
  merged.equity_history = mergePracticeEquityRows(live, other)
  const liveDailyRows = Array.isArray(live.daily_equity_history)
    ? live.daily_equity_history
    : other.daily_equity_history
  merged.daily_equity_history = mergePracticeDailyRows([], liveDailyRows).slice(-500)
  const modes = new Set([current.snapshot_mode, incoming.snapshot_mode].filter(Boolean))
  merged.snapshot_mode = modes.size > 1 || modes.has('merged')
    ? 'merged'
    : (live.snapshot_mode || '')
  merged.equity_history_scope = live.equity_history_scope || 'latest_day'
  merged.last_error = live.last_error || ''
  return merged
}
