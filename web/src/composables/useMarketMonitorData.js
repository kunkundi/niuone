import { reactive } from 'vue'
import { useDashboardTabs } from './useDashboardTabs.js'
import { messagePageRevision, revisionKey } from '../utils/messageRevision.js'

const CATEGORY = 'market_monitor'
const HISTORY_LIMIT = 200
const REFRESH_INTERVAL_MS = 15 * 1000
const SUMMARY_REFRESH_INTERVAL_MS = 5 * 60 * 1000
const CACHE_TTL_MS = 5 * 60 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const CACHE_KEY = 'niuniu-dashboard-market-page-v2'

const state = reactive({
  loading: true,
  loaded: false,
  records: [],
  total: 0,
  categories: {},
  generatedAt: '',
  revision: '',
  error: '',
  summary: { loading: true },
})

let users = 0
let refreshTimer = 0
let historyController = null
let revisionController = null
let summaryController = null
let loadSequence = 0
let lastHistoryLoadAt = 0
let lastSummaryLoadAt = 0
let revisionRequest = null
let summaryRequest = null

function setMarketCategoryCount(total) {
  useDashboardTabs().setCategoryCount(CATEGORY, ` · ${Number(total || 0)}`)
}

function publishMessageCategoryCounts(categories = state.categories) {
  const { setCategoryCount } = useDashboardTabs()
  for (const category of ['market_monitor', 'x_monitor', 'us_ratings']) {
    const value = categories?.[category]
    if (!value || typeof value !== 'object') continue
    setCategoryCount(category, ` · ${Number(value.count || 0)}`)
  }
}

function publishLastUpdated(value = state.generatedAt) {
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(value || '').slice(11, 19) || '--' },
  }))
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      records: state.records,
      total: state.total,
      categories: state.categories,
      generatedAt: state.generatedAt,
      revision: state.revision,
      summary: state.summary,
      lastHistoryLoadAt,
      lastSummaryLoadAt,
      savedAt: Date.now(),
    }))
  } catch {}
}

function restoreCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || '{}')
    if (!cached.savedAt || Date.now() - Number(cached.savedAt) > CACHE_TTL_MS) return
    state.records = Array.isArray(cached.records) ? cached.records : []
    state.total = Number(cached.total || 0)
    state.categories = cached.categories || {}
    state.generatedAt = String(cached.generatedAt || '')
    state.revision = String(cached.revision || '')
    state.summary = cached.summary || state.summary
    state.loading = false
    state.loaded = true
    lastHistoryLoadAt = Number(cached.lastHistoryLoadAt || cached.savedAt || 0)
    lastSummaryLoadAt = Number(cached.lastSummaryLoadAt || 0)
    publishMessageCategoryCounts()
  } catch {}
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

async function loadHistory({ background = false } = {}) {
  const sequence = ++loadSequence
  historyController?.abort()
  const controller = new AbortController()
  historyController = controller
  if (!background || !state.records.length) state.loading = true
  try {
    const payload = await fetchJson(
      `/api/messages?limit=${HISTORY_LIMIT}&offset=0&category=${CATEGORY}`,
      controller,
    )
    if (sequence !== loadSequence) return
    const records = Array.isArray(payload?.records) ? payload.records : []
    state.records = records
    state.categories = payload?.categories || {}
    state.total = Number(state.categories?.[CATEGORY]?.count || records.length)
    state.generatedAt = String(payload?.generated_at || '')
    state.revision = messagePageRevision(payload, CATEGORY)
    state.error = ''
    state.loading = false
    state.loaded = true
    lastHistoryLoadAt = Date.now()
    publishMessageCategoryCounts()
    publishLastUpdated()
    saveCache()
  } catch (error) {
    if (error?.name === 'AbortError') return
    if (sequence !== loadSequence) return
    state.error = String(error?.message || error)
    state.loading = false
    state.loaded = true
  } finally {
    if (historyController === controller) historyController = null
  }
}

async function pollRevision() {
  if (revisionRequest || historyController) return revisionRequest
  const controller = new AbortController()
  revisionController = controller
  const request = fetchJson(`/api/messages/revision?category=${CATEGORY}`, controller)
    .then(revision => {
      if (revisionKey(revision) !== state.revision) {
        return loadHistory({ background: state.records.length > 0 })
      }
      state.error = ''
      setMarketCategoryCount(revision.count)
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

async function loadSummary({ force = false } = {}) {
  if (summaryRequest) return summaryRequest
  if (!force && lastSummaryLoadAt && Date.now() - lastSummaryLoadAt < SUMMARY_REFRESH_INTERVAL_MS) return null
  const controller = new AbortController()
  summaryController = controller
  if (!state.summary.generated_at && !state.summary.summary) {
    state.summary = { ...state.summary, loading: true }
  }
  const request = fetchJson('/api/us_market_summary', controller)
    .then(payload => {
      state.summary = { ...(payload || { available: false }), loading: false }
      lastSummaryLoadAt = Date.now()
      saveCache()
      return payload
    })
    .catch(error => {
      if (error?.name !== 'AbortError' && !state.summary.generated_at && !state.summary.summary) {
        state.summary = { available: false, error: String(error?.message || error), loading: false }
      }
      return null
    })
    .finally(() => {
      if (summaryController === controller) summaryController = null
      if (summaryRequest === request) summaryRequest = null
    })
  summaryRequest = request
  return request
}

function refreshMarketMonitor() {
  return Promise.allSettled([
    pollRevision(),
    loadSummary(),
  ])
}

function activateMarketMonitor() {
  users += 1
  if (users > 1) return
  if (state.loaded && state.records.length) {
    publishLastUpdated()
    pollRevision()
    loadSummary()
  } else {
    loadHistory().finally(() => loadSummary())
  }
  refreshTimer = window.setInterval(refreshMarketMonitor, REFRESH_INTERVAL_MS)
}

function deactivateMarketMonitor() {
  users = Math.max(0, users - 1)
  if (users) return
  window.clearInterval(refreshTimer)
  refreshTimer = 0
  loadSequence += 1
  historyController?.abort()
  revisionController?.abort()
  summaryController?.abort()
  historyController = null
  revisionController = null
  summaryController = null
}

restoreCache()

export function useMarketMonitorData() {
  return {
    state,
    activateMarketMonitor,
    deactivateMarketMonitor,
    refreshMarketMonitor,
    refreshHistory: loadHistory,
  }
}
