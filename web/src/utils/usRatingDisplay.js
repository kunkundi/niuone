export function ratingDateKey(record) {
  const time = String(record?.time || '').trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(time)) return time.slice(0, 10)
  const timestamp = Number(record?.timestamp || 0)
  if (Number.isFinite(timestamp) && timestamp > 0) {
    const date = new Date(timestamp * 1000)
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  }
  return '未知日期'
}

export function groupRatingRecordsByDay(records) {
  const groups = new Map()
  for (const record of records || []) {
    const key = ratingDateKey(record)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(record)
  }
  return groups
}

export function shortRatingDate(day) {
  const value = String(day || '')
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return `${Number(value.slice(5, 7))}/${Number(value.slice(8, 10))}`
  }
  return value || '--'
}

export function cleanRatingValue(value) {
  return String(value || '')
    .replace(/^[-–—]\s*/, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function extractTargetPrice(text) {
  const source = String(text || '')
    .replace(/,/g, '')
    .split(/此前|原为|previously|from\s+\$?/i)[0]
  const arrows = Array.from(source.matchAll(/(?:→|->|至|到|上调至|提高至)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)/g))
  if (arrows.length) {
    const number = Number(arrows.at(-1)[1])
    if (Number.isFinite(number)) return number
  }
  const patterns = [
    /\$\s*([0-9]+(?:\.[0-9]+)?)/g,
    /([0-9]+(?:\.[0-9]+)?)\s*(?:美元|美金|usd)/gi,
    /(?:目标价)\s*([0-9]+(?:\.[0-9]+)?)/g,
  ]
  let best = null
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const number = Number(match[1])
      if (Number.isFinite(number)) best = number
    }
  }
  return best
}

export function parseRatingReport(content) {
  const rawLines = String(content || '').split('\n')
  const lines = rawLines.map(line => line.replace(/\s+$/g, ''))
  const stockHeaderPattern = /^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}[A-Z][A-Z0-9.]{0,8}\s*(?:\/|（|\()\s*[A-Z]/
  const boldStockPattern = /^\*{1,2}\s*(?:\d+[）.)]\s*)?[A-Z][A-Z0-9.]{1,8}\s*(?:[/(（]|\/\s*[A-Z])/
  const plainStockPattern = /^([A-Z][A-Z0-9.]{1,8})\s*\/\s*([A-Z][^：:*]{1,100})$/
  const fields = [
    ['analyst', /^[-–—\s*]*(?:\*\*)?机构\/分析师(?:\*\*)?[:：](.*)$/],
    ['action', /^[-–—\s*]*(?:\*\*)?评级动作(?:\*\*)?[:：](.*)$/],
    ['target', /^[-–—\s*]*(?:\*\*)?目标价(?:\*\*)?[:：](.*)$/],
    ['reason', /^[-–—\s*]*(?:\*\*)?核心理由\/催化剂(?:\*\*)?[:：](.*)$/],
    ['risk', /^[-–—\s*]*(?:\*\*)?风险点(?:\*\*)?[:：](.*)$/],
    ['type', /^[-–—\s*]*(?:\*\*)?适合关注类型(?:\*\*)?[:：](.*)$/],
  ]

  function nextNonemptyLine(index) {
    for (let next = index + 1; next < lines.length; next += 1) {
      const value = lines[next].trim()
      if (value) return value
    }
    return ''
  }

  function isPlainStockHeader(line, index) {
    return plainStockPattern.test(line.trim()) && fields[0][1].test(nextNonemptyLine(index))
  }

  function parseStockHeader(line, index) {
    let match = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*[（(]\s*([^)）]+?)\s*[)）]\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/)
    if (!match) {
      match = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*\/\s*([^*：:]+?)\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/)
    }
    if (match) {
      return {
        name: `${match[1].toUpperCase()} / ${cleanRatingValue(match[2] || '')}`,
        inline: cleanRatingValue(match[3] || ''),
      }
    }
    const oldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}([A-Z][A-Z0-9.]{0,8}\s*\/\s*[A-Z][^：:]+?)(?:\*{0,2})\s*(?:[:：](.*))?$/)
    if (oldMatch) {
      return { name: cleanRatingValue(oldMatch[1]), inline: cleanRatingValue(oldMatch[2] || '') }
    }
    const plainMatch = isPlainStockHeader(line, index) ? line.match(plainStockPattern) : null
    if (plainMatch) {
      return {
        name: `${plainMatch[1].toUpperCase()} / ${cleanRatingValue(plainMatch[2])}`,
        inline: '',
      }
    }
    let boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{1,8})\s*\/\s*([^：:]+?)(?:\s*\*{1,2}|\s*[:：]|$)/)
    if (!boldMatch) {
      boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{1,8})\s*[（(]\s*([^)）]+?)\s*[)）]/)
    }
    if (!boldMatch) return null
    const ticker = boldMatch[1].toUpperCase()
    const company = cleanRatingValue(boldMatch[2] || '')
    const rest = line.slice(boldMatch[0].length).trim()
    const inlineMatch = rest.match(/^[\s\S]*?[:：]\s*(.*)/)
    return {
      name: ticker + (company ? ` / ${company}` : ''),
      inline: cleanRatingValue(inlineMatch ? inlineMatch[1] : ''),
    }
  }

  const firstStockIndex = lines.findIndex((line, index) => {
    const value = line.trim()
    return stockHeaderPattern.test(value)
      || boldStockPattern.test(value)
      || isPlainStockHeader(value, index)
  })
  if (firstStockIndex < 0) return null

  const intro = lines.slice(0, firstStockIndex).join('\n').replace(/^-{3,}\s*$/gm, '').trim()
  const introLines = intro.split('\n').map(line => line.trim()).filter(Boolean)
  const title = (introLines[0] || '机构买入评级').replace(/^标题[:：]\s*/, '')
  const summary = introLines.slice(1).join('\n\n')
  const items = []
  let current = null
  let activeKey = ''

  for (let index = firstStockIndex; index < lines.length; index += 1) {
    const line = lines[index].trim()
    if (!line || /^-{3,}$/.test(line)) continue
    const parsed = parseStockHeader(line, index)
    if (parsed) {
      if (/报道|来源|链接|检索|摘要/.test(parsed.name)) continue
      if (current) items.push(current)
      current = { name: parsed.name }
      activeKey = ''
      if (parsed.inline) {
        const sentences = parsed.inline.split(/[；;。]/).map(cleanRatingValue).filter(Boolean)
        for (const sentence of sentences) {
          if (/目标价|\$\s*\d|\d+(?:\.\d+)?\s*(?:美元|美金)/i.test(sentence) && !current.target) current.target = sentence
          else if (/机构|分析师|\/\s*[A-Z][A-Za-z .]+/.test(sentence) && !current.analyst) current.analyst = sentence
          else if (/评级|上调|维持|新覆盖|Buy|Overweight|Outperform|Neutral|Underperform/i.test(sentence) && !current.action) current.action = sentence
          else if (/风险/.test(sentence) && !current.risk) current.risk = sentence.replace(/^风险是?/, '')
          else if (/适合关注类型/.test(sentence) && !current.type) current.type = sentence.replace(/^适合关注类型[:：]?/, '')
          else if (!current.reason) current.reason = sentence
          else current.reason = cleanRatingValue(`${current.reason}；${sentence}`)
        }
      }
      continue
    }
    if (!current) continue
    let matched = false
    for (const [key, pattern] of fields) {
      const fieldMatch = line.match(pattern)
      if (!fieldMatch) continue
      current[key] = cleanRatingValue(fieldMatch[1])
      activeKey = key
      matched = true
      break
    }
    if (!matched && activeKey) {
      current[activeKey] = cleanRatingValue(`${current[activeKey] || ''} ${line}`)
    }
  }
  if (current) items.push(current)
  const validItems = items.filter(item => /^[A-Z][A-Z0-9.]{1,8}\s*\/?\s*/.test(item.name))
  return validItems.length ? { title, summary, items: validItems } : null
}

export function ratingTicker(item) {
  return String((item?.name || '').split('/')[0] || '').trim().toUpperCase()
}

export function ratingSymbolsFromRecords(records) {
  const symbols = new Set()
  for (const record of records || []) {
    const report = parseRatingReport(record?.content || '')
    for (const item of report?.items || []) {
      const ticker = ratingTicker(item)
      if (/^[A-Z][A-Z0-9.]{1,8}$/.test(ticker)) symbols.add(ticker)
    }
  }
  return [...symbols]
}

function safeDomIdPart(value) {
  return String(value || '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'row'
}

export function ratingStableRowId(reportKey, ticker, index) {
  return `rating-${safeDomIdPart(reportKey)}-${safeDomIdPart(ticker)}-${index}`
}

export function ratingRecordKey(record) {
  return String(
    record?.id
      || record?.raw_path
      || record?.external_id
      || `us_ratings:${record?.timestamp || ''}:${record?.content_hash || ''}`,
  )
}

export function ratingCompanyDetail(ticker, company, quote) {
  const lines = [`股票代码：${ticker}`]
  const companyName = cleanRatingValue(company)
  const sector = cleanRatingValue(quote?.sector)
  const industry = cleanRatingValue(quote?.industry)
  if (companyName) lines.push(`公司：${companyName}`)
  if (sector || industry) lines.push(`分类：${[sector, industry].filter(Boolean).join(' / ')}`)
  return lines.join('\n')
}

export function ratingMetaDetail(item) {
  const lines = []
  const analyst = cleanRatingValue(item?.analyst)
  const type = cleanRatingValue(item?.type)
  if (analyst) lines.push(`机构 / 分析师：${analyst}`)
  if (type) lines.push(`关注类型：${type}`)
  return lines.join('\n')
}

export function ratingTextSegments(value) {
  const source = String(value || '')
  const pattern = /(\*\*(.+?)\*\*|`([^`]+)`)/g
  const segments = []
  let last = 0
  for (const match of source.matchAll(pattern)) {
    const start = match.index || 0
    if (start > last) segments.push({ kind: 'text', text: source.slice(last, start) })
    segments.push({ kind: match[2] ? 'strong' : 'code', text: match[2] || match[3] || '' })
    last = start + match[0].length
  }
  if (last < source.length) segments.push({ kind: 'text', text: source.slice(last) })
  return segments
}
