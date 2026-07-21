export function messageRecordKey(record) {
  return String(
    record?.id
      || record?.raw_path
      || record?.external_id
      || `${record?.category || ''}:${record?.session_id || ''}:${record?.timestamp || ''}:${String(record?.content || '').slice(0, 80)}`,
  )
}

export function shortHash(text) {
  let hash = 2166136261
  const source = String(text || '')
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

export function xRecordKey(record) {
  return `x-${shortHash(messageRecordKey(record))}`
}

export function cleanXLine(line) {
  return String(line || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/^#{1,6}\s*/, '')
    .replace(/^[│┃┌└↳\-–—━\s]+/u, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function normalizeXMarker(line) {
  return cleanXLine(line).replace(/^【([^】]+)】/, '$1｜').trim()
}

export function xLineRole(line) {
  const source = normalizeXMarker(line)
  if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(source)) return 'original'
  if (/^回复(?:\s*[|｜:：]|$)/.test(source)) return 'reply'
  return ''
}

export function isXNoiseLine(line) {
  const source = cleanXLine(line)
  return !source
    || /^X Watchlist Dashboard Archive$/i.test(source)
    || /^Cron Job:/i.test(source)
    || /^Job ID:/i.test(source)
    || /^Run Time:/i.test(source)
    || /^Mode:/i.test(source)
    || /^Status:/i.test(source)
    || /^发现 X 账号新推文/.test(source)
    || /^X 新推文 \d+/.test(source)
}

export function xParts(line) {
  return normalizeXMarker(line).split(/[｜|]/).map(value => value.trim()).filter(Boolean)
}

export function xIsTimePart(part) {
  const source = String(part || '').trim()
  return /\d{4}-\d{2}-\d{2}/.test(source) || /^时间未知$/.test(source)
}

export function xCleanAuthorPart(part) {
  return String(part || '')
    .replace(/^(?:引用)?原[贴帖]\s*[:：]?/, '')
    .replace(/^回复\s*[:：]?/, '')
    .replace(/^评论\/转述\s*[:：]?/, '')
    .trim()
}

function xLooksLikeRolePart(part) {
  const source = xCleanAuthorPart(part)
  return /^(?:回复|评论\/转述|转述|评论|引用|原[贴帖]|引用原[贴帖])$/.test(source) || !source
}

function xHeaderAuthor(parts, role) {
  if (!parts.length) return ''
  if (parts.length >= 3 || role === 'reply' || role === 'original' || xLooksLikeRolePart(parts[0])) {
    const found = parts.find((part, index) => index > 0 && !xIsTimePart(part))
    return xCleanAuthorPart(found || parts[1] || parts[0])
  }
  if (parts.length === 2 && xIsTimePart(parts[1])) return xCleanAuthorPart(parts[0])
  return xCleanAuthorPart(parts.find(part => /^@/.test(part)) || parts[0])
}

export function xPostMeta(record) {
  const metadata = record && typeof record.metadata === 'object' && record.metadata
    ? record.metadata
    : {}
  return metadata && typeof metadata.post === 'object' && metadata.post ? metadata.post : {}
}

function xMetadataAuthor(record) {
  const post = xPostMeta(record)
  const direct = String(post.display_name || '').trim()
  if (direct) return direct
  const sourceLabel = String(record?.source_label || '').trim()
  if (sourceLabel && sourceLabel !== '推特监控' && sourceLabel !== 'X 监控') return sourceLabel
  const handle = String(record?.metadata?.handle || post.handle || record?.source_id || '').trim()
  return handle && !/^cron_/i.test(handle) ? `@${handle.replace(/^@/, '')}` : ''
}

export function truncateText(text, maxLength = 180) {
  const source = String(text || '').replace(/\s+/g, ' ').trim()
  return source.length > maxLength ? `${source.slice(0, maxLength - 1)}…` : source
}

export function summarizeXRecord(record) {
  const lines = String(record?.content || '')
    .split('\n')
    .map(cleanXLine)
    .filter(line => line && !isXNoiseLine(line))
  const replyIndex = lines.findIndex(line => xLineRole(line) === 'reply')
  const originalIndex = lines.findIndex(line => xLineRole(line) === 'original')
  const headerIndex = replyIndex >= 0
    ? replyIndex
    : (originalIndex >= 0 ? originalIndex : lines.findIndex(line => line.includes('｜') || line.includes('|')))
  const headerLine = headerIndex >= 0 ? lines[headerIndex] : (lines[0] || '')
  const parts = xParts(headerLine)
  const role = xLineRole(headerLine)
  let author = xHeaderAuthor(parts, role)
  if (!author || xIsTimePart(author)) author = xMetadataAuthor(record)
  author = author || 'X'
  const timeFromHeader = parts.find(part => /\d{4}-\d{2}-\d{2}/.test(part))
  const bodyStart = headerIndex >= 0 ? headerIndex + 1 : 0
  let bodyLines = lines.slice(bodyStart)
    .filter(line => !xLineRole(line) && !isXNoiseLine(line) && !/^[-━└]+$/.test(line))
  if (!bodyLines.length) bodyLines = lines.filter(line => !xLineRole(line) && !isXNoiseLine(line))
  const preview = truncateText(bodyLines.join(' '), 190) || '暂无正文'
  const label = role === 'reply'
    ? '回复'
    : (role === 'original' && headerLine.includes('引用') ? '引用' : '推文')
  const initialSource = author.replace(/^@/, '').trim()
  return {
    author,
    time: timeFromHeader || record?.time || '',
    preview,
    label,
    threaded: originalIndex >= 0 && replyIndex >= 0,
    initial: (initialSource[0] || 'X').toUpperCase(),
  }
}

export function cleanXMediaUrl(url) {
  let source = String(url || '').trim().replace(/\\\//g, '/')
  if (!/^https?:\/\//i.test(source)) return ''
  if (
    source.includes('pbs.twimg.com/media/')
    && !source.includes('?')
    && !/:(?:large|small|medium|orig)$/i.test(source)
    && /\.(?:jpg|jpeg|png|webp)$/i.test(source)
  ) {
    source += ':large'
  }
  return source
}

export function isXPostMediaUrl(url) {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:'
      && parsed.hostname === 'pbs.twimg.com'
      && /^\/(?:media|ext_tw_video_thumb|tweet_video_thumb)\//.test(parsed.pathname)
  } catch {
    return false
  }
}

export function xMediaItems(items) {
  if (!Array.isArray(items)) return []
  const seen = new Set()
  const output = []
  for (const item of items) {
    if (!item || typeof item !== 'object') continue
    const url = cleanXMediaUrl(item.url || '')
    const type = String(item.type || '').trim() || 'image'
    if (!isXPostMediaUrl(url) || seen.has(url)) continue
    seen.add(url)
    output.push({ url, type })
  }
  return output.slice(0, 8)
}

export function xMediaDisplayUrl(url) {
  return `/api/x_media?url=${encodeURIComponent(url)}`
}

export function xMediaGroups(record) {
  const post = xPostMeta(record)
  return [
    { key: 'reply_to_media', label: '原帖图片', items: xMediaItems(post.reply_to_media) },
    { key: 'quoted_media', label: '引用图片', items: xMediaItems(post.quoted_media) },
    { key: 'media', label: '推文图片', items: xMediaItems(post.media) },
  ].filter(group => group.items.length)
}

export function xAllMediaItems(record) {
  return xMediaGroups(record).flatMap(group => group.items)
}

export function parseXThread(content) {
  const lines = String(content || '').split('\n')
  let inOriginal = false
  let inReply = false
  const originalLines = []
  const replyLines = []
  for (const line of lines) {
    const trimmed = line.trim()
    const marker = normalizeXMarker(trimmed)
    if (!marker || /^[-━└]+$/.test(marker)) continue
    if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = true
      inReply = false
      if (/[|｜：:]/.test(marker)) originalLines.push(marker)
      continue
    }
    if (/^回复(?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = false
      inReply = true
      if (/[|｜：:]/.test(marker)) replyLines.push(marker)
      continue
    }
    const bodyLine = trimmed.replace(/^[│┃]\s?/u, '').trim()
    if (inOriginal && bodyLine) originalLines.push(bodyLine)
    else if (inReply && bodyLine) replyLines.push(bodyLine)
  }
  return {
    originalPost: originalLines.length && replyLines.length ? originalLines.join('\n').trim() : null,
    reply: originalLines.length && replyLines.length ? replyLines.join('\n').trim() : null,
  }
}

export function stripXCurrentPostHeader(text) {
  const lines = String(text || '').split('\n')
  if (!lines.length) return ''
  const firstLine = lines[0] || ''
  const isEmojiHeader = /^[\p{Emoji}\uFE0F\u200D]+\s*\*\*.+?\*\*/u.test(firstLine)
  if (xLineRole(firstLine) === 'reply' || firstLine.includes('｜') || firstLine.includes('|') || isEmojiHeader) {
    return lines.slice(1).join('\n').trim()
  }
  return String(text || '').trim()
}

export function clampXImageZoom(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 1
  return Math.max(0.5, Math.min(3, Math.round(number * 100) / 100))
}

export function xPageRevisionKey(revision) {
  return JSON.stringify({
    category: String(revision?.category || ''),
    count: Number(revision?.count || 0),
    page: {
      limit: Number(revision?.page?.limit || 0),
      offset: Number(revision?.page?.offset || 0),
      count: Number(revision?.page?.count || 0),
      fingerprint: String(revision?.page?.fingerprint || ''),
    },
  })
}
