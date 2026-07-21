export const PRACTICE_BUY_NAMES = {
  trend_pullback: '趋势回踩',
  breakout: '突破确认',
  shaofu_b1: '少妇B1',
  b2_confirm: 'B2确认',
  b3_accelerate: 'B3中继',
  super_b1: '超级B1',
  li_daxiao_bottom: '李大霄',
  tide_leader: '主线领航',
  tide_rotation: '轮动初升',
  tide_recovery: '冰点修复',
  mixed: '混合买入',
  unknown_buy: '未识别买入',
  auto_exit: '系统退出',
  unknown: '其他',
}

export const PRACTICE_EXIT_NAMES = {
  stop_loss: '止损',
  take_profit: '主动止盈',
  profit_protection: '回撤保护',
  top_escape: '逃顶/出货',
  technical_break: '技术破位',
  sell_score: '卖出评分',
  no_progress: '信号未兑现',
  position_adjust: '仓位调整',
  model_sell: '模型卖出',
  sector_retreat: '板块退潮',
  market_risk: '市场风险',
  other_exit: '其他卖出',
}

export function formatPracticeNumber(value, digits = 2) {
  const number = Number(value)
  return Number.isFinite(number)
    ? Number(number.toFixed(digits)).toLocaleString('en')
    : '--'
}

export function formatPracticeAmount(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return Math.abs(number) >= 10_000
    ? `${(number / 10_000).toFixed(2)}万`
    : number.toFixed(2)
}

export function signedPracticeNumber(value, suffix = '%') {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${number >= 0 ? '+' : ''}${formatPracticeNumber(number)}${suffix}`
}

export function signedPracticeAmount(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${number >= 0 ? '+' : ''}${formatPracticeAmount(number)}`
}

export function practiceValueColor(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '#94a3b8'
  return number >= 0 ? '#ff4d4f' : '#39d98a'
}

export function splitPracticeTags(value) {
  if (Array.isArray(value)) return value.map(item => String(item || '').trim()).filter(Boolean)
  return String(value || '').split(/[，,]/).map(item => item.trim()).filter(Boolean)
}

export function uniquePracticeLabels(values) {
  return [...new Set((values || []).filter(Boolean))]
}

export function inferPracticeExitRules(reason) {
  const text = String(reason || '')
  const rules = []
  const add = rule => { if (rule && !rules.includes(rule)) rules.push(rule) }
  if (/止损|破入场止损/.test(text)) add('stop_loss')
  if (/止盈清仓|第一批止盈|卤煮止盈|止盈/.test(text)) add('take_profit')
  if (/峰值回撤|ATR吊灯|移动止损保本|盈转亏/.test(text)) add('profit_protection')
  if (/S1|S2|S3|逃顶|出货五式/.test(text)) add('top_escape')
  if (/卖出评分|防卖飞评分/.test(text)) add('sell_score')
  if (/BBI|白线|死叉|低点跌破|趋势确认失效/.test(text)) add('technical_break')
  if (/未兑现|低效持仓|持仓到期|次日不涨|未延续/.test(text)) add('no_progress')
  return rules
}
