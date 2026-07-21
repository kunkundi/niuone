import { reactive } from 'vue'
import { useDashboardTabs } from './useDashboardTabs.js'
import { messagePageRevision, revisionKey } from '../utils/messageRevision.js'
import { ratingSymbolsFromRecords } from '../utils/usRatingDisplay.js'

const CATEGORY = 'us_ratings'
const HISTORY_LIMIT = 120
const REFRESH_INTERVAL_MS = 10 * 60 * 1000
const CACHE_TTL_MS = 10 * 60 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const CACHE_KEY = 'niuniu-dashboard-us-ratings-v1'

const state = reactive({
  loading: true,
  loaded: false,
  records: [],
  total: 0,
  categories: {},
  generatedAt: '',
  revision: '',
  error: '',
  quotes: {},
  quoteSymbols: [],
  profiles: {},
  profileSymbols: [],
})

let users = 0
let refreshTimer = 0
let historyController = null
let revisionController = null
let loadSequence = 0
let revisionRequest = null
const auxiliaryControllers = new Set()
const quoteRequests = new Map()
const profileRequests = new Map()

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

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      records: state.records,
      total: state.total,
      categories: state.categories,
      generatedAt: state.generatedAt,
      revision: state.revision,
      quotes: state.quotes,
      quoteSymbols: state.quoteSymbols,
      profiles: state.profiles,
      profileSymbols: state.profileSymbols,
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
    state.quotes = cached.quotes || {}
    state.quoteSymbols = Array.isArray(cached.quoteSymbols) ? cached.quoteSymbols : []
    state.profiles = cached.profiles || {}
    state.profileSymbols = Array.isArray(cached.profileSymbols) ? cached.profileSymbols : []
    state.loading = false
    state.loaded = true
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
    publishMessageCategoryCounts()
    publishLastUpdated()
    saveCache()
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return
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
      useDashboardTabs().setCategoryCount(CATEGORY, ` · ${Number(revision.count || 0)}`)
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

async function loadAuxiliary(kind, symbols) {
  const normalized = [...new Set(symbols || [])]
    .map(symbol => String(symbol || '').trim().toUpperCase())
    .filter(symbol => /^[A-Z][A-Z0-9.]{1,8}$/.test(symbol))
  const loaded = new Set(kind === 'quotes' ? state.quoteSymbols : state.profileSymbols)
  const items = kind === 'quotes' ? state.quotes : state.profiles
  const missing = normalized.filter(symbol => !loaded.has(symbol) && !items[symbol])
  if (!missing.length) return false
  const key = missing.join(',')
  const requests = kind === 'quotes' ? quoteRequests : profileRequests
  if (requests.has(key)) return requests.get(key)

  const controller = new AbortController()
  auxiliaryControllers.add(controller)
  const endpoint = kind === 'quotes' ? '/api/us_quotes' : '/api/us_profiles'
  const request = fetchJson(`${endpoint}?symbols=${encodeURIComponent(key)}`, controller)
    .then(payload => {
      const nextItems = payload?.items || {}
      const returnedSymbols = Array.isArray(payload?.symbols) ? payload.symbols : missing
      if (kind === 'quotes') {
        state.quotes = { ...state.quotes, ...nextItems }
        state.quoteSymbols = [...new Set([...state.quoteSymbols, ...returnedSymbols])]
      } else {
        state.profiles = { ...state.profiles, ...nextItems }
        state.profileSymbols = [...new Set([...state.profileSymbols, ...returnedSymbols])]
      }
      saveCache()
      return true
    })
    .catch(error => {
      if (error?.name !== 'AbortError') console.error(`us ${kind} load error`, error)
      return false
    })
    .finally(() => {
      auxiliaryControllers.delete(controller)
      if (requests.get(key) === request) requests.delete(key)
    })
  requests.set(key, request)
  return request
}

function loadQuotesForRecords(records) {
  return loadAuxiliary('quotes', ratingSymbolsFromRecords(records))
}

function loadProfile(ticker) {
  return loadAuxiliary('profiles', [ticker])
}

function activateUsRatings() {
  users += 1
  if (users > 1) return
  if (state.loaded && state.records.length) {
    publishMessageCategoryCounts()
    publishLastUpdated()
    pollRevision()
  } else {
    loadHistory()
  }
  refreshTimer = window.setInterval(pollRevision, REFRESH_INTERVAL_MS)
}

function deactivateUsRatings() {
  users = Math.max(0, users - 1)
  if (users) return
  window.clearInterval(refreshTimer)
  refreshTimer = 0
  loadSequence += 1
  historyController?.abort()
  revisionController?.abort()
  for (const controller of auxiliaryControllers) controller.abort()
  auxiliaryControllers.clear()
  historyController = null
  revisionController = null
}

restoreCache()

export function useUsRatingsData() {
  return {
    state,
    activateUsRatings,
    deactivateUsRatings,
    refreshUsRatings: pollRevision,
    loadQuotesForRecords,
    loadProfile,
  }
}
