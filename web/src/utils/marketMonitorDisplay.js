function truncateText(text, maxLength = 180) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim()
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength - 1)}…`
    : normalized
}

export function cleanMarketLine(line) {
  return String(line || '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function recordKey(record) {
  return String(
    record?.id
      || record?.raw_path
      || record?.external_id
      || `${record?.category || ''}:${record?.session_id || ''}:${record?.timestamp || ''}:${(record?.content || '').slice(0, 80)}`,
  )
}

function shortHash(text) {
  let hash = 2166136261
  const source = String(text || '')
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

export function marketRecordKey(record) {
  return `market-${shortHash(recordKey(record))}`
}

export function marketReportType(record, content = '') {
  const identity = [
    record?.title,
    record?.chat_label,
    record?.metadata?.job_name,
    String(content || '').split('\n').slice(0, 3).join(' '),
  ].map(value => String(value || '').trim()).filter(Boolean).join(' ')
  const source = [
    record?.source_id,
    record?.external_id,
    record?.delivery?.job_id,
    record?.metadata?.run_key,
  ].map(value => String(value || '').trim()).filter(Boolean).join(' ')
  if (/98f0c8a12d3e/.test(source) || /隔夜美股|美股盘面/.test(identity)) return '美股'
  if (/192abba7eeb5/.test(source) || /午盘/.test(identity)) return '午盘'
  if (/67ac98149ead/.test(source) || /盘后|收盘/.test(identity)) return '盘后'
  if (/8453b3f28cd3/.test(source) || /竞价|盘前/.test(identity)) return '竞价'
  return '盘面'
}

function marketSectionLines(lines, headingText, limit = 3) {
  const start = lines.findIndex(line => cleanMarketLine(line).includes(headingText))
  if (start < 0) return []
  const result = []
  for (const raw of lines.slice(start + 1)) {
    const line = cleanMarketLine(raw)
    if (!line) continue
    if (/\*\*.+\*\*/.test(raw) || /^[📊🔥💰⚡📈💡⚠️🌡️📌👀ℹ️]/u.test(line)) break
    result.push(line)
    if (result.length >= limit) break
  }
  return result
}

export function summarizeMarketRecord(record) {
  const raw = String(record?.content || '')
  const lines = raw.split('\n').map(line => line.trim()).filter(Boolean)
  const cleanLines = lines.map(cleanMarketLine).filter(Boolean)
  const titleLine = cleanLines[0] || '盘面监控'
  const title = titleLine
    .replace(/^牛牛大王[，,]\s*/, '')
    .replace(/来了[:：]?$/, '')
    .trim() || '盘面监控'
  const timeLine = cleanLines.find(line => /\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(line)) || ''
  const timeMatch = timeLine.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/)
  const mood = cleanLines.find(line => line.startsWith('💬')) || ''
  const overview = cleanLines.find(line => /^样本\s/.test(line) || /^涨停池\s/.test(line)) || ''
  const volume = cleanLines.find(line => /成交额\s/.test(line)) || ''
  const chips = []
  for (const line of [overview, volume, ...marketSectionLines(lines, '热门板块', 3)]) {
    const text = truncateText(line.replace(/^💬\s*/, ''), 34)
    if (text) chips.push(text)
  }
  return {
    title,
    type: marketReportType(record, raw),
    time: timeMatch ? timeMatch[0] : (record?.time || ''),
    preview: truncateText((mood || overview || titleLine).replace(/^💬\s*/, ''), 150),
    chips: chips.slice(0, 5),
  }
}

function marketLeadingIcon(text) {
  const match = String(text || '').match(/^(📊|🔥|💰|⚡|📈|💡|⚠️|⚠|🌡️|🌡|📌|👀|ℹ️|ℹ)\s*/u)
  return match
    ? { icon: match[1], rest: String(text || '').slice(match[0].length).trim() }
    : { icon: '', rest: String(text || '').trim() }
}

function marketSectionTone(title, icon) {
  const source = `${title || ''} ${icon || ''}`
  if (/风险|⚠/.test(source)) return 'risk'
  if (/资金/.test(source)) return 'flow'
  if (/热门|强势|封单|热度|🔥|⚡|🌡|📌/.test(source)) return 'hot'
  if (/操作|提示|观察|💡|👀/.test(source)) return 'tip'
  if (/概况|情绪|📊/.test(source)) return 'overview'
  return ''
}

function marketHeadingInfo(raw) {
  const clean = cleanMarketLine(raw)
  if (!clean) return null
  const leading = marketLeadingIcon(clean)
  const titleSource = leading.rest || clean
  const titleParts = titleSource.split(/[·|]/).map(value => value.trim()).filter(Boolean)
  const title = (titleParts[0] || titleSource).replace(/[:：]$/, '').trim()
  const markdownHeading = /\*\*.+\*\*/.test(String(raw || ''))
  const knownHeading = /^(市场概况|竞价情绪|开盘价强弱|热门板块|竞价强势板块|资金流向|强势个股|成交活跃|竞价成交活跃|操作提示|风险|复合热度|涨停封单|封单|跌停风险|重点观察)/.test(title)
  if (!markdownHeading && !knownHeading) return null
  return {
    title: title || '盘面小节',
    meta: titleParts.slice(1).join(' · '),
    icon: leading.icon || '•',
    tone: marketSectionTone(title, leading.icon),
  }
}

export function parseMarketDetail(content) {
  const sections = []
  const intro = []
  let current = null
  const pushCurrent = () => {
    if (current && (current.items.length || current.meta)) sections.push(current)
    current = null
  }
  for (const raw of String(content || '').split('\n')) {
    if (!String(raw || '').trim()) continue
    const heading = marketHeadingInfo(raw)
    if (heading) {
      pushCurrent()
      current = { ...heading, items: [] }
      continue
    }
    const clean = cleanMarketLine(raw)
    if (!clean) continue
    if (current) current.items.push(clean)
    else intro.push(clean)
  }
  pushCurrent()
  return { intro, sections }
}

export function marketMoodLine(sections) {
  for (const section of sections || []) {
    for (const line of section.items || []) {
      const clean = cleanMarketLine(line)
      if (/^💬/.test(clean)) return clean.replace(/^💬\s*/, '').trim()
    }
  }
  return ''
}

function marketMetricTone(label, value) {
  const number = Number(String(value || '').replace(/[^\d.-]/g, ''))
  if (/上涨|涨停/.test(label)) return 'up'
  if (/下跌|跌停/.test(label)) return 'down'
  if (Number.isFinite(number) && number > 0 && /^\+/.test(String(value || '').trim())) return 'up'
  if (Number.isFinite(number) && number < 0) return 'down'
  return ''
}

export function marketSummaryMetrics(sections) {
  const overview = (sections || []).find(section => /市场概况|竞价情绪/.test(section.title)) || sections?.[0]
  if (!overview) return []
  const metrics = []
  const seen = new Set()
  for (const line of overview.items || []) {
    const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim()
    for (const part of clean.split(/[|·]/).map(value => value.trim()).filter(Boolean)) {
      const match = part.match(/^(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*([+\-]?\d[\d,.]*(?:\.\d+)?\s*(?:只|亿手|万手|手|亿|万亿|万|%)?)/)
      if (!match || seen.has(match[1])) continue
      seen.add(match[1])
      metrics.push({
        label: match[1],
        value: match[2].replace(/\s+/g, ''),
        tone: marketMetricTone(match[1], match[2]),
      })
      if (metrics.length >= 8) return metrics
    }
  }
  return metrics
}

function isMarketMetricLine(line) {
  const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim()
  return /(?:^|[|·]\s*)(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*[+\-]?\d/.test(clean)
}

export function marketSectionDisplayItems(section) {
  const overview = /市场概况|竞价情绪/.test(section?.title || '')
  return (section?.items || []).filter(line => {
    const clean = cleanMarketLine(line)
    if (/^💬/.test(clean)) return false
    if (overview && isMarketMetricLine(clean)) return false
    return true
  })
}

export function marketDetailLine(text, sectionTone = '') {
  const clean = cleanMarketLine(text).replace(/^·\s*/, '').trim()
  if (!clean) return null
  const flow = clean.match(/^(流入|流出)[:：]\s*(.+)$/)
  if (flow) {
    return {
      kind: 'flow',
      label: flow[1],
      segments: marketSignedTextSegments(flow[2], { colorUnsignedMoney: true }),
    }
  }
  return {
    kind: 'item',
    note: /^数据暂不可用|^数据为|^ℹ️|^ℹ/.test(clean),
    risk: sectionTone === 'risk',
    tip: sectionTone === 'tip',
    segments: marketSignedTextSegments(clean, {
      colorUnsignedMoney: sectionTone === 'flow' || /净额/.test(clean),
    }),
  }
}

export function marketSignedTextSegments(text, { colorUnsignedMoney = false } = {}) {
  const source = String(text || '')
  const pattern = /((?:sh|sz|bj)?\d{6}\s+[*A-Za-z\u4e00-\u9fa5][*A-Za-z0-9\u4e00-\u9fa5·]{1,12})|([+\-]\d[\d,.]*(?:\.\d+)?\s*(?:%|万亿|亿手|万手|手|亿|万|元)?|\d[\d,.]*(?:\.\d+)?\s*(?:万亿|亿手|万手|手|亿))/gi
  const result = []
  let last = 0
  for (const match of source.matchAll(pattern)) {
    const token = match[0]
    const start = match.index || 0
    if (start > last) result.push({ text: source.slice(last, start), kind: 'text', tone: '' })
    if (match[1]) {
      result.push({ text: token, kind: 'symbol', tone: '' })
    } else {
      const compact = token.replace(/\s+/g, '')
      const unsignedMoney = !/^[+\-]/.test(compact) && /(?:万亿|亿)$/.test(compact)
      const tone = compact.startsWith('-')
        ? 'down'
        : (compact.startsWith('+') || (colorUnsignedMoney && unsignedMoney) ? 'up' : '')
      result.push({ text: token, kind: 'number', tone })
    }
    last = start + token.length
  }
  if (last < source.length) result.push({ text: source.slice(last), kind: 'text', tone: '' })
  return result
}

export function isUsMarketSummaryRecord(record) {
  const title = String(record?.title || record?.chat_label || '').trim()
  const sourceId = String(record?.source_id || '').trim()
  const jobId = String(record?.delivery?.job_id || record?.metadata?.job_id || '').trim()
  return title === '隔夜美股盘面总结'
    || sourceId === 'cron_output_98f0c8a12d3e'
    || jobId === '98f0c8a12d3e'
}

export function usMarketSummaryMatchesDay(day, summaryData = {}) {
  const selectedDay = String(day || '').slice(0, 10)
  const targetDay = String(summaryData?.target_cn_date || '').slice(0, 10)
  return Boolean(selectedDay && targetDay && selectedDay === targetDay)
}

export function marketDateKey(record) {
  const time = String(record?.time || '').trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(time)) return time.slice(0, 10)
  const contentDate = String(record?.content || '').match(/\d{4}-\d{2}-\d{2}/)
  if (contentDate) return contentDate[0]
  const timestamp = Number(record?.timestamp || 0)
  if (Number.isFinite(timestamp) && timestamp > 0) {
    const date = new Date(timestamp * 1000)
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  }
  return '未知日期'
}

export function groupMarketRecordsByDay(records) {
  const groups = new Map()
  for (const record of records || []) {
    const key = marketDateKey(record)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(record)
  }
  return groups
}
