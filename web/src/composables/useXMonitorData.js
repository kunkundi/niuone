import { reactive } from 'vue'
import { useDashboardTabs } from './useDashboardTabs.js'
import { xPageRevisionKey } from '../utils/xMonitorDisplay.js'

export const X_MONITOR_PAGE_SIZE = 10
const CATEGORY = 'x_monitor'
const REFRESH_INTERVAL_MS = 15 * 1000
const CACHE_TTL_MS = 5 * 60 * 1000
const CACHE_MAX_ENTRIES = 6
const REQUEST_TIMEOUT_MS = 15 * 1000
const CACHE_KEY = 'niuniu-dashboard-x-pages-v2'

const state = reactive({
  loading: true,
  loaded: false,
  records: [],
  total: 0,
  categories: {},
  generatedAt: '',
  offset: 0,
  revision: '',
  error: '',
})

let users = 0
let refreshTimer = 0
let pageController = null
let revisionController = null
let loadSequence = 0
let revisionRequest = null
let pageCache = {}
const prefetchRequests = new Map()
const prefetchControllers = new Set()

function publishMessageCategoryCounts(categories = state.categories) {
  const { setCategoryCount } = useDashboardTabs()
  for (const category of ['market_monitor', 'x_monitor', 'us_ratings']) {
    const value = categories?.[category]
    if (!value || typeof value !== 'object') continue
    setCategoryCount(category, ` · ${Number(value.count || 0)}`)
  }
}

function publishLastUpdated() {
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(state.generatedAt || '').slice(11, 19) || '--' },
  }))
}

function normalizeOffset(value) {
  const number = Math.max(0, Number(value || 0))
  return Math.floor(number / X_MONITOR_PAGE_SIZE) * X_MONITOR_PAGE_SIZE
}

function rememberPage(offset, payload, savedAt = Date.now()) {
  const key = String(normalizeOffset(offset))
  pageCache[key] = { payload, savedAt: Number(savedAt || Date.now()) }
  const entries = Object.entries(pageCache)
    .sort((left, right) => Number(right[1]?.savedAt || 0) - Number(left[1]?.savedAt || 0))
    .slice(0, CACHE_MAX_ENTRIES)
  pageCache = Object.fromEntries(entries)
  saveCache()
}

function cachedPage(offset) {
  const key = String(normalizeOffset(offset))
  const entry = pageCache[key]
  if (!entry) return null
  if (Date.now() - Number(entry.savedAt || 0) > CACHE_TTL_MS) {
    delete pageCache[key]
    saveCache()
    return null
  }
  return entry.payload || null
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ pageCache, savedAt: Date.now() }))
  } catch {}
}

function restoreCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || '{}')
    if (!cached.savedAt || Date.now() - Number(cached.savedAt) > CACHE_TTL_MS) return
    for (const [offset, entry] of Object.entries(cached.pageCache || {})) {
      if (!entry?.payload || Date.now() - Number(entry.savedAt || 0) > CACHE_TTL_MS) continue
      pageCache[String(normalizeOffset(offset))] = entry
    }
  } catch {}
}

function applyPayload(payload, offset) {
  const records = Array.isArray(payload?.records) ? payload.records : []
  state.records = records
  state.categories = payload?.categories || {}
  state.total = Number(state.categories?.[CATEGORY]?.count || payload?.matched_total || records.length)
  state.generatedAt = String(payload?.generated_at || '')
  state.offset = normalizeOffset(offset)
  state.revision = xPageRevisionKey(payload?.revision)
  state.error = ''
  state.loading = false
  state.loaded = true
  publishMessageCategoryCounts()
  publishLastUpdated()
}

async function fetchJson(url, controller) {
  let timedOut = false
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return await response.json()
  } catch (error) {
    if (timedOut) throw new Error('请求超时')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

function messagesUrl(offset) {
  return `/api/messages?limit=${X_MONITOR_PAGE_SIZE}&offset=${normalizeOffset(offset)}&category=${CATEGORY}`
}

function revisionUrl(offset) {
  return `/api/messages/revision?category=${CATEGORY}&limit=${X_MONITOR_PAGE_SIZE}&offset=${normalizeOffset(offset)}`
}

function prefetchPage(offset, total) {
  const targetOffset = normalizeOffset(offset)
  if (targetOffset >= Number(total || 0) || cachedPage(targetOffset)) return null
  const key = String(targetOffset)
  if (prefetchRequests.has(key)) return prefetchRequests.get(key)
  const controller = new AbortController()
  prefetchControllers.add(controller)
  const request = fetchJson(messagesUrl(targetOffset), controller)
    .then(payload => {
      rememberPage(targetOffset, payload)
      return payload
    })
    .catch(() => null)
    .finally(() => {
      prefetchControllers.delete(controller)
      if (prefetchRequests.get(key) === request) prefetchRequests.delete(key)
    })
  prefetchRequests.set(key, request)
  return request
}

function prefetchAdjacentPages(offset, total) {
  if (!total) return
  prefetchPage(offset - X_MONITOR_PAGE_SIZE, total)
  prefetchPage(offset + X_MONITOR_PAGE_SIZE, total)
}

async function loadPage(offset, { background = false } = {}) {
  const targetOffset = normalizeOffset(offset)
  const sequence = ++loadSequence
  const previous = {
    records: state.records,
    total: state.total,
    categories: state.categories,
    generatedAt: state.generatedAt,
    offset: state.offset,
    revision: state.revision,
    loaded: state.loaded,
  }
  pageController?.abort()
  const controller = new AbortController()
  pageController = controller
  if (!background || !state.records.length) state.loading = true
  try {
    const payload = await fetchJson(messagesUrl(targetOffset), controller)
    if (sequence !== loadSequence) return false
    applyPayload(payload, targetOffset)
    rememberPage(targetOffset, payload)
    prefetchAdjacentPages(targetOffset, state.total)
    return true
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return false
    state.error = String(error?.message || error)
    state.loading = false
    if (!background && previous.loaded) Object.assign(state, previous)
    else state.loaded = true
    return false
  } finally {
    if (pageController === controller) pageController = null
  }
}

async function pollRevision({ offset = state.offset } = {}) {
  const targetOffset = normalizeOffset(offset)
  if (revisionRequest || pageController) return revisionRequest
  const controller = new AbortController()
  revisionController = controller
  const request = fetchJson(revisionUrl(targetOffset), controller)
    .then(revision => {
      if (targetOffset !== state.offset) return null
      if (xPageRevisionKey(revision) !== state.revision) {
        return loadPage(targetOffset, { background: state.records.length > 0 })
      }
      state.error = ''
      state.total = Number(revision.count || state.total)
      useDashboardTabs().setCategoryCount(CATEGORY, ` · ${state.total}`)
      return null
    })
    .catch(error => {
      if (error?.name !== 'AbortError') state.error = String(error?.message || error)
      return null
    })
    .finally(() => {
      if (revisionController === controller) revisionController = null
      if (revisionRequest === request) revisionRequest = null
    })
  revisionRequest = request
  return request
}

async function selectXPage(offset) {
  const targetOffset = normalizeOffset(offset)
  if (targetOffset === state.offset && state.loaded) return true
  loadSequence += 1
  pageController?.abort()
  revisionController?.abort()
  revisionRequest = null
  const cached = cachedPage(targetOffset)
  if (cached) {
    applyPayload(cached, targetOffset)
    prefetchAdjacentPages(targetOffset, state.total)
    await pollRevision({ offset: targetOffset })
    return true
  }
  return loadPage(targetOffset)
}

function activateXMonitor(offset = 0) {
  users += 1
  if (users > 1) return
  const targetOffset = normalizeOffset(offset)
  const cached = cachedPage(targetOffset)
  if (cached) {
    applyPayload(cached, targetOffset)
    prefetchAdjacentPages(targetOffset, state.total)
    pollRevision({ offset: targetOffset })
  } else {
    loadPage(targetOffset)
  }
  refreshTimer = window.setInterval(pollRevision, REFRESH_INTERVAL_MS)
}

function deactivateXMonitor() {
  users = Math.max(0, users - 1)
  if (users) return
  window.clearInterval(refreshTimer)
  refreshTimer = 0
  loadSequence += 1
  pageController?.abort()
  revisionController?.abort()
  revisionRequest = null
  for (const controller of prefetchControllers) controller.abort()
  prefetchControllers.clear()
  pageController = null
  revisionController = null
}

restoreCache()

export function useXMonitorData() {
  return {
    state,
    activateXMonitor,
    deactivateXMonitor,
    refreshXMonitor: pollRevision,
    selectXPage,
  }
}
