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
let auxiliaryController = null
let auxiliaryPromise = null
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

function loadAuxiliaryMarketData() {
  if (auxiliaryPromise) return auxiliaryPromise
  const generation = auxiliaryGeneration
  const controller = new AbortController()
  auxiliaryController = controller
  const isCurrent = () => (
    generation === auxiliaryGeneration && auxiliaryController === controller
  )
  const requests = []

  if (isDue(
    state.marketBreadth.timeline?.length,
    marketBreadthLastFetchAt,
    MARKET_BREADTH_REFRESH_INTERVAL_MS,
  )) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/market_breadth', { latest: {}, timeline: [] }, controller.signal),
      (marketBreadth) => {
        state.marketBreadth = marketBreadth.error && state.marketBreadth.timeline?.length
          ? { ...state.marketBreadth, error: marketBreadth.error }
          : marketBreadth
        if (!marketBreadth.error) marketBreadthLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }
  if (isDue(
    (state.sectors.gain_top || state.sectors.sectors || state.sectors.items || []).length,
    sectorsLastFetchAt,
    SECTORS_REFRESH_INTERVAL_MS,
  )) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/sectors', { sectors: [] }, controller.signal),
      (sectors) => {
        state.sectors = sectors
        if (!sectors.error) sectorsLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }
  if (isDue(state.usSectors.items?.length, usSectorsLastFetchAt, US_SECTORS_REFRESH_INTERVAL_MS)) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/us_sectors', { items: [] }, controller.signal),
      (usSectors) => {
        state.usSectors = usSectors
        if (!usSectors.error) usSectorsLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }
  if (isDue(state.hotStocks.items?.length, hotStocksLastFetchAt, HOT_STOCKS_REFRESH_INTERVAL_MS)) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/hot_stocks', { items: [] }, controller.signal),
      (hotStocks) => {
        state.hotStocks = hotStocks
        if (!hotStocks.error) hotStocksLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }
  if (isDue(
    state.marketFlow.total_inflow_yi != null,
    marketFlowLastFetchAt,
    MARKET_FLOW_REFRESH_INTERVAL_MS,
  )) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/market_flow', { total_inflow_yi: null }, controller.signal),
      (marketFlow) => {
        state.marketFlow = marketFlow
        if (!marketFlow.error) marketFlowLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }
  if (isDue(
    state.moneyFlow.inflow?.length || state.moneyFlow.outflow?.length,
    moneyFlowLastFetchAt,
    MONEY_FLOW_REFRESH_INTERVAL_MS,
  )) {
    requests.push(applyPayloadAsReady(
      fetchJson('/api/money_flow', { inflow: [], outflow: [] }, controller.signal),
      (moneyFlow) => {
        state.moneyFlow = moneyFlow
        if (!moneyFlow.error) moneyFlowLastFetchAt = Date.now()
      },
      isCurrent,
    ))
  }

  const pending = Promise.all(requests)
    .catch((error) => {
      if (error?.name !== 'AbortError') console.error('market auxiliary data failed', error)
    })
    .finally(() => {
      if (auxiliaryController === controller) auxiliaryController = null
      if (auxiliaryPromise === pending) auxiliaryPromise = null
    })
  auxiliaryPromise = pending
  return pending
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
  auxiliaryController?.abort()
  auxiliaryController = null
  auxiliaryPromise = null
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
