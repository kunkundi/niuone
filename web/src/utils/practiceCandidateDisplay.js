export const PRACTICE_STRATEGY_META = {
  trend_pullback: { label: '趋势回踩', color: '#60a5fa' },
  breakout: { label: '突破确认', color: '#ec4899' },
  shaofu_b1: { label: '少妇B1', color: '#f97316' },
  b2_confirm: { label: 'B2确认', color: '#22c55e' },
  b3_accelerate: { label: 'B3中继', color: '#a78bfa' },
  super_b1: { label: '超级B1', color: '#fb7185' },
  tide_leader: { label: '主线领航', color: '#06b6d4' },
  tide_rotation: { label: '轮动初升', color: '#14b8a6' },
  tide_recovery: { label: '冰点修复', color: '#22d3ee' },
}

export const PRACTICE_STOCK_BOARD_LABELS = {
  main_board: '主板',
  chi_next: '创业板',
  star_market: '科创板',
  st: 'ST',
}

export const PRACTICE_TIDE_STATUS_LABELS = {
  leading: '领先',
  improving: '改善',
  weakening: '转弱',
  lagging: '落后',
}

export function formatPracticeNumber(value, digits = 2) {
  const number = Number(value)
  return Number.isFinite(number)
    ? Number(number.toFixed(digits)).toLocaleString('en')
    : '--'
}

export function practiceCandidateTier(item) {
  const score = Number(item?.best_score ?? item?.score ?? 0)
  const threshold = Number(item?.entry_threshold ?? 8)
  const hardBlockers = Array.isArray(item?.hard_blockers) ? item.hard_blockers : []
  if (item?.actionable && !hardBlockers.length && score >= threshold) return 'high'
  return score >= threshold - 1.5 ? 'mid' : 'low'
}

export function practiceCandidateTierCounts(items) {
  const counts = { high: 0, mid: 0, low: 0 }
  for (const item of Array.isArray(items) ? items : []) counts[practiceCandidateTier(item)] += 1
  return counts
}

export function practiceCandidateStrategyMeta(payloadMeta = {}) {
  return { ...PRACTICE_STRATEGY_META, ...(payloadMeta || {}) }
}

export function practiceCandidateIndustryLabel(item = {}) {
  const label = item.industry || item.sector || item.board_label || item.board || ''
  return PRACTICE_STOCK_BOARD_LABELS[label] || label
}
