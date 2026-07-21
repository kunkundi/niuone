export function numberValue(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function formatNumber(value, digits = 2) {
  const number = numberValue(value)
  return number === null ? '--' : Number(number.toFixed(digits)).toLocaleString('en')
}

export function formatAmount(value) {
  const number = numberValue(value)
  if (number === null) return '--'
  return Math.abs(number) >= 10000
    ? `${(number / 10000).toFixed(2)}万`
    : number.toFixed(2)
}

export function signedNumber(value, suffix = '', digits = 2) {
  const number = numberValue(value)
  return number === null ? '--' : `${number > 0 ? '+' : ''}${formatNumber(number, digits)}${suffix}`
}

export function toneClass(value, prefix = '') {
  const number = numberValue(value)
  const tone = number > 0 ? 'up' : number < 0 ? 'down' : 'flat'
  return prefix ? `${prefix}-${tone}` : tone
}

export function legacyMarketType(item = {}) {
  const key = String(item.key || '')
  const code = String(item.code || '')
  const name = String(item.name || '')
  if (item.market_type) return item.market_type
  if (key === 'a50_fut' || code === 'hf_CHA50CFD' || /A50|富时中国/.test(name)) return 'a_futures'
  if (/_fut$/.test(key) || /期货/.test(name)) return 'us_futures'
  if (['dow', 'nas', 'spx'].includes(key) || /^us/.test(code)) return 'us_index'
  if (key === 'xau' || key === 'brent' || /黄金|伦敦金|原油/.test(name)) return 'commodity'
  if (item.group === 'domestic' || /^s[hz]/.test(code)) return 'a_index'
  return item.group || ''
}

export function marketItems(payload, type, fallbackGroup = '') {
  const grouped = payload?.market_groups?.[type]
  if (Array.isArray(grouped) && grouped.length) return grouped
  const items = Array.isArray(payload?.items) ? payload.items : []
  return items.filter(item => {
    const marketType = legacyMarketType(item)
    return marketType === type || (fallbackGroup && item.group === fallbackGroup && marketType === type)
  })
}

function marketClockParts(timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(new Date())
  const pick = type => parts.find(part => part.type === type)?.value || ''
  let hour = Number(pick('hour'))
  const minute = Number(pick('minute'))
  if (hour === 24) hour = 0
  return { weekday: pick('weekday'), minuteOfDay: hour * 60 + minute }
}

function isWeekday(clock) {
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(clock.weekday)
}

export function indicesSwitchSession(aIndexItems = []) {
  const aShare = marketClockParts('Asia/Shanghai')
  const aMinute = aShare.minuteOfDay
  const hasAIndexItems = !Array.isArray(aIndexItems) || aIndexItems.length > 0
  const aShareOpen = isWeekday(aShare)
    && ((aMinute >= 9 * 60 + 15 && aMinute <= 11 * 60 + 30)
      || (aMinute >= 13 * 60 && aMinute <= 15 * 60))
  const aShareDay = isWeekday(aShare) && aMinute >= 9 * 60 + 15 && aMinute <= 15 * 60
  if (aShareOpen || (aShareDay && hasAIndexItems)) return 'a_share'

  const us = marketClockParts('America/New_York')
  const usOpen = isWeekday(us) && us.minuteOfDay >= 9 * 60 + 30 && us.minuteOfDay <= 16 * 60
  return usOpen ? 'us_open' : 'global'
}
