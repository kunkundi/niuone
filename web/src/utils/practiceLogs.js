import { currentChinaDateKey } from './practiceChart.js'
import { formatPracticeAmount, formatPracticeNumber } from './practiceDisplay.js'

function compactText(value, limit = 120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…` : text
}

export function practiceOperationLogDate(payload) {
  const generatedDate = String(payload?.generated_at || '').slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(generatedDate) ? generatedDate : currentChinaDateKey()
}

function tradeLogEntry(trade, index) {
  const action = String(trade.action || '').toUpperCase()
  const isBuy = action === 'BUY'
  const isSell = action === 'SELL'
  const actionLabel = isBuy ? '买入' : isSell ? '卖出' : '成交'
  const codeName = [trade.code, trade.name].map(value => String(value || '').trim()).filter(Boolean).join(' ')
  const shares = trade.shares == null ? '' : `${trade.shares}股`
  const price = Number(trade.price)
  const amount = Number(trade.amount)
  const pnl = Number(trade.pnl)
  const details = [
    Number.isFinite(price) ? `价 ${formatPracticeNumber(price, 3)}` : '',
    shares,
    Number.isFinite(amount) ? `额 ${formatPracticeAmount(amount)}` : '',
    isSell && Number.isFinite(pnl) ? `盈亏 ${pnl >= 0 ? '+' : ''}${formatPracticeAmount(pnl)}` : '',
    compactText(trade.reason || trade.trade_reason || '', 100),
  ].filter(Boolean)
  return {
    key: `trade-${index}`,
    time: String(trade.time || ''),
    kind: 'trade',
    raw: trade,
    badgeClass: isBuy ? 'buy' : isSell ? 'sell' : 'trade',
    badge: actionLabel,
    summary: `${actionLabel} ${codeName || '--'}${shares ? ` · ${shares}` : ''}`,
    detail: details.join('｜'),
    order: index,
  }
}

function executableActionCount(actions) {
  return actions.filter(action => ['BUY', 'SELL'].includes(
    String(action?.action || action?.type || '').toUpperCase(),
  )).length
}

function blockedReasons(decision, actions) {
  const raw = Array.isArray(decision.execution_blocked_reasons)
    ? decision.execution_blocked_reasons
    : (decision.execution_blocked_reason ? [decision.execution_blocked_reason] : [])
  const byCode = new Map(actions.map(action => [String(action?.code || '').trim(), action || {}]))
  return [...new Set(raw.map(item => String(item || '').trim()).filter(Boolean))].map(text => {
    const match = text.match(/^(\d{6})[:：]\s*(.*)$/)
    if (!match) return text
    const action = byCode.get(match[1]) || {}
    return `${[match[1], action.name].filter(Boolean).join(' ')}：${match[2] || '执行拦截'}`
  })
}

function decisionLogEntry(entry, index) {
  const decision = entry.decision || {}
  const actions = Array.isArray(decision.actions) ? decision.actions : []
  const executed = Array.isArray(entry.executed) ? entry.executed : []
  const blocked = blockedReasons(decision, actions)
  const suggestedCount = executableActionCount(actions)
  const actionText = [
    suggestedCount ? `建议${suggestedCount}笔` : '',
    executed.length ? `执行${executed.length}笔` : '',
    blocked.length ? `拦截${blocked.length}笔` : '',
  ].filter(Boolean).join(' / ') || '无成交'
  const refinement = decision.buy_refinement || {}
  const dropped = Array.isArray(refinement.dropped) ? refinement.dropped : []
  const kept = Array.isArray(refinement.kept_codes) ? refinement.kept_codes : []
  const refinementText = dropped.length || kept.length
    ? ['二次取舍', kept.length ? `保留${kept.join('、')}` : '未保留新仓', dropped.length ? `放弃${dropped.map(item => [item.code, item.name].filter(Boolean).join(' ')).join('、')}` : '', compactText(refinement.summary || refinement.reason || '', 90)].filter(Boolean).join('：')
    : ''
  const executionTimes = [...new Set(executed.map(item => String(item?.time || '').slice(11, 19)).filter(Boolean))]
  const executionRange = executionTimes.length
    ? (executionTimes.length === 1 ? executionTimes[0] : `${executionTimes[0]}-${executionTimes.at(-1)}`)
    : ''
  const executionNote = executionRange && executionRange !== String(entry.time || '').slice(11, 19)
    ? `成交时间${executionRange}`
    : ''
  return {
    key: `decision-${index}`,
    time: String(entry.time || ''),
    kind: 'decision',
    raw: entry,
    badgeClass: 'decision',
    badge: '决策',
    summary: compactText(decision.summary || entry.trade_reason || '模型决策', 120),
    detail: [
      compactText(entry.trade_reason || '', 90),
      actionText,
      refinementText,
      blocked.length ? `拦截：${compactText(blocked.join('；'), 140)}` : '',
      executionNote,
      decision.error ? compactText(decision.error, 90) : '',
    ].filter(Boolean).join('｜'),
    order: index,
  }
}

export function normalizePracticeOperationLogs(payload) {
  const date = practiceOperationLogDate(payload)
  const entries = []
  ;(payload?.trade_log || []).forEach((trade, index) => {
    if (trade && String(trade.time || '').slice(0, 10) === date) entries.push(tradeLogEntry(trade, index))
  })
  ;(payload?.decision_log || []).forEach((entry, index) => {
    if (entry && String(entry.time || '').slice(0, 10) === date) entries.push(decisionLogEntry(entry, index + 10_000))
  })
  return entries.sort((left, right) => right.time.localeCompare(left.time) || left.order - right.order)
}

function textValue(value) {
  if (value == null) return ''
  if (Array.isArray(value)) return value.map(textValue).filter(Boolean).join('；')
  if (typeof value === 'object') return textValue(value.summary || value.reason || value.detail || '')
  return String(value || '').trim()
}

export function practiceLogRawText(item) {
  const raw = item?.raw && typeof item.raw === 'object' ? item.raw : {}
  if (item?.kind === 'trade') return textValue(raw.reason || raw.trade_reason || item.detail || item.summary)
  const decision = raw.decision && typeof raw.decision === 'object' ? raw.decision : {}
  const parts = [
    textValue(decision.summary),
    textValue(raw.trade_reason),
    textValue(decision.execution_blocked_reasons || decision.execution_blocked_reason),
    textValue(decision.buy_refinement),
    textValue(decision.error),
  ].filter(Boolean)
  return [...new Set(parts)].join('\n\n') || item?.detail || item?.summary || '无原文'
}
