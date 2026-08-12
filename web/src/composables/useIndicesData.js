import { reactive } from 'vue'
import { applyPayloadAsReady } from '../utils/asyncPayload.js'
import { startVisiblePolling } from '../utils/visiblePolling.js'

const REFRESH_INTERVAL_MS = 15 * 1000
const MONEY_FLOW_REFRESH_INTERVAL_MS = 60 * 1000
const MARKET_BREADTH_REFRESH_INTERVAL_MS = 30 * 1000
const SECTORS_REFRESH_INTERVAL_MS = 60 * 1000
const HOT_STOCKS_REFRESH_INTERVAL_MS = 60 * 1000
const US_SECTORS_REFRESH_INTERVAL_MS = 5 * 60 * 1000
const MARKET_FLOW_REFRESH_INTERVAL_MS = 30 * 1000

const state = reactive({
  loading: false,
  loaded: false,
  indices: { items: [] },
  marketBreadth: { latest: {}, timeline: [] },
  sectors: { sectors: [] },
  usSectors: { items: [] },
  hotStocks: { items: [] },
  moneyFlow: { inflow: [], outflow: [] },
  marketFlow: { total_inflow_yi: null },
})
const view = reactive({
  panel: 'index',
  indexPriorityOverride: '',
  marketRegionOverride: '',
})

let users = 0
let stopRefreshPolling = null
let requestController = null
const auxiliaryRequests = new Map()
let auxiliaryGeneration = 0
let loadSequence = 0
let lastLoadedAt = 0
let moneyFlowLastFetchAt = 0
let marketBreadthLastFetchAt = 0
let sectorsLastFetchAt = 0
let hotStocksLastFetchAt = 0
let usSectorsLastFetchAt = 0
let marketFlowLastFetchAt = 0

function fallbackWithError(fallback, error) {
  return { ...fallback, error: String(error) }
}

async function fetchJson(url, fallback, signal) {
  try {
    const response = await fetch(url, {
      signal,
      credentials: 'same-origin',
    })
    if (!response.ok) return fallbackWithError(fallback, `HTTP ${response.status}`)
    return await response.json()
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    return fallbackWithError(fallback, error)
  }
}

function publishLastUpdated(payload) {
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(payload?.generated_at || '').slice(11, 19) || '--' },
  }))
}

function isDue(hasRows, lastFetchAt, intervalMs) {
  return !hasRows || Date.now() - lastFetchAt >= intervalMs
}

function loadAuxiliaryPayload(key, fetchPayload, apply) {
  const activeRequest = auxiliaryRequests.get(key)
  if (activeRequest) return activeRequest.promise

  const generation = auxiliaryGeneration
  const controller = new AbortController()
  const request = { controller, promise: null }
  const isCurrent = () => (
    generation === auxiliaryGeneration && auxiliaryRequests.get(key) === request
  )
  request.promise = applyPayloadAsReady(
    fetchPayload(controller.signal),
    apply,
    isCurrent,
  )
    .catch((error) => {
      if (error?.name !== 'AbortError') console.error(`${key} data failed`, error)
    })
    .finally(() => {
      if (auxiliaryRequests.get(key) === request) auxiliaryRequests.delete(key)
    })
  auxiliaryRequests.set(key, request)
  return request.promise
}

function loadAuxiliaryMarketData() {
  const requests = []

  if (isDue(
    state.marketBreadth.timeline?.length,
    marketBreadthLastFetchAt,
    MARKET_BREADTH_REFRESH_INTERVAL_MS,
  )) {
    requests.push(loadAuxiliaryPayload(
      'market breadth',
      signal => fetchJson('/api/market_breadth', { latest: {}, timeline: [] }, signal),
      (marketBreadth) => {
        state.marketBreadth = marketBreadth.error && state.marketBreadth.timeline?.length
          ? { ...state.marketBreadth, error: marketBreadth.error }
          : marketBreadth
        if (!marketBreadth.error) marketBreadthLastFetchAt = Date.now()
      },
    ))
  }
  if (isDue(
    (state.sectors.gain_top || state.sectors.sectors || state.sectors.items || []).length,
    sectorsLastFetchAt,
    SECTORS_REFRESH_INTERVAL_MS,
  )) {
    requests.push(loadAuxiliaryPayload(
      'sectors',
      signal => fetchJson('/api/sectors', { sectors: [] }, signal),
      (sectors) => {
        state.sectors = sectors
        if (!sectors.error) sectorsLastFetchAt = Date.now()
      },
    ))
  }
  if (isDue(state.usSectors.items?.length, usSectorsLastFetchAt, US_SECTORS_REFRESH_INTERVAL_MS)) {
    requests.push(loadAuxiliaryPayload(
      'US sectors',
      signal => fetchJson('/api/us_sectors', { items: [] }, signal),
      (usSectors) => {
        state.usSectors = usSectors
        if (!usSectors.error) usSectorsLastFetchAt = Date.now()
      },
    ))
  }
  if (isDue(state.hotStocks.items?.length, hotStocksLastFetchAt, HOT_STOCKS_REFRESH_INTERVAL_MS)) {
    requests.push(loadAuxiliaryPayload(
      'hot stocks',
      signal => fetchJson('/api/hot_stocks', { items: [] }, signal),
      (hotStocks) => {
        state.hotStocks = hotStocks
        if (!hotStocks.error) hotStocksLastFetchAt = Date.now()
      },
    ))
  }
  if (isDue(
    state.marketFlow.total_inflow_yi != null,
    marketFlowLastFetchAt,
    MARKET_FLOW_REFRESH_INTERVAL_MS,
  )) {
    requests.push(loadAuxiliaryPayload(
      'market flow',
      signal => fetchJson('/api/market_flow', { total_inflow_yi: null }, signal),
      (marketFlow) => {
        state.marketFlow = marketFlow
        if (!marketFlow.error) marketFlowLastFetchAt = Date.now()
      },
    ))
  }
  if (isDue(
    state.moneyFlow.inflow?.length || state.moneyFlow.outflow?.length,
    moneyFlowLastFetchAt,
    MONEY_FLOW_REFRESH_INTERVAL_MS,
  )) {
    requests.push(loadAuxiliaryPayload(
      'money flow',
      signal => fetchJson('/api/money_flow', { inflow: [], outflow: [] }, signal),
      (moneyFlow) => {
        state.moneyFlow = moneyFlow
        if (!moneyFlow.error) moneyFlowLastFetchAt = Date.now()
      },
    ))
  }

  return Promise.all(requests)
}

async function loadIndices({ background = false } = {}) {
  const sequence = ++loadSequence
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  if (!background) state.loading = true

  try {
    const nextIndices = await fetchJson('/api/indices', { items: [] }, controller.signal)
    if (sequence !== loadSequence) return
    state.indices = nextIndices.error && background && state.indices.items?.length
      ? { ...state.indices, error: nextIndices.error }
      : nextIndices
    state.loaded = true
    state.loading = false
    publishLastUpdated(state.indices)

    loadAuxiliaryMarketData()
    lastLoadedAt = Date.now()
  } catch (error) {
    if (error?.name === 'AbortError') return
    state.indices = state.indices.items?.length
      ? { ...state.indices, error: String(error) }
      : { items: [], error: String(error) }
    state.loaded = true
    state.loading = false
  } finally {
    if (requestController === controller) requestController = null
  }
}

function activateIndices() {
  users += 1
  if (users > 1) return
  const background = state.indices.items?.length > 0
  if (!lastLoadedAt || Date.now() - lastLoadedAt >= REFRESH_INTERVAL_MS) {
    loadIndices({ background })
  } else {
    publishLastUpdated(state.indices)
  }
  stopRefreshPolling = startVisiblePolling(
    () => loadIndices({ background: true }),
    REFRESH_INTERVAL_MS,
  )
}

function deactivateIndices() {
  users = Math.max(0, users - 1)
  if (users) return
  stopRefreshPolling?.()
  stopRefreshPolling = null
  requestController?.abort()
  requestController = null
  auxiliaryGeneration += 1
  for (const request of auxiliaryRequests.values()) request.controller.abort()
  auxiliaryRequests.clear()
}

function adoptMoneyFlow(payload) {
  state.moneyFlow = payload || { inflow: [], outflow: [] }
  moneyFlowLastFetchAt = Date.now()
}

export function useIndicesData() {
  return {
    state,
    view,
    activateIndices,
    deactivateIndices,
    refreshIndices: loadIndices,
    adoptMoneyFlow,
  }
}
