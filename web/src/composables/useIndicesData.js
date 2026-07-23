import { reactive } from 'vue'
import { applyPayloadAsReady } from '../utils/asyncPayload.js'
import { startVisiblePolling } from '../utils/visiblePolling.js'

const REFRESH_INTERVAL_MS = 15 * 1000
const MONEY_FLOW_REFRESH_INTERVAL_MS = 60 * 1000
const MARKET_BREADTH_REFRESH_INTERVAL_MS = 60 * 1000

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
let loadSequence = 0
let lastLoadedAt = 0
let moneyFlowLastFetchAt = 0
let marketBreadthLastFetchAt = 0

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

    const hasMoneyFlowRows = state.moneyFlow.inflow?.length || state.moneyFlow.outflow?.length
    const moneyFlowDue = !hasMoneyFlowRows
      || Date.now() - moneyFlowLastFetchAt >= MONEY_FLOW_REFRESH_INTERVAL_MS
    const marketBreadthDue = !state.marketBreadth.timeline?.length
      || Date.now() - marketBreadthLastFetchAt >= MARKET_BREADTH_REFRESH_INTERVAL_MS
    const isCurrent = () => sequence === loadSequence
    const requests = []
    if (marketBreadthDue) {
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
    requests.push(
      applyPayloadAsReady(
        fetchJson('/api/sectors', { sectors: [] }, controller.signal),
        (sectors) => { state.sectors = sectors },
        isCurrent,
      ),
      applyPayloadAsReady(
        fetchJson('/api/us_sectors', { items: [] }, controller.signal),
        (usSectors) => { state.usSectors = usSectors },
        isCurrent,
      ),
      applyPayloadAsReady(
        fetchJson('/api/hot_stocks', { items: [] }, controller.signal),
        (hotStocks) => { state.hotStocks = hotStocks },
        isCurrent,
      ),
      applyPayloadAsReady(
        fetchJson('/api/market_flow', { total_inflow_yi: null }, controller.signal),
        (marketFlow) => { state.marketFlow = marketFlow },
        isCurrent,
      ),
    )
    if (moneyFlowDue) {
      requests.push(applyPayloadAsReady(
        fetchJson('/api/money_flow', { inflow: [], outflow: [] }, controller.signal),
        (moneyFlow) => {
          state.moneyFlow = moneyFlow
          if (!moneyFlow.error) moneyFlowLastFetchAt = Date.now()
        },
        isCurrent,
      ))
    }
    await Promise.all(requests)
    if (!isCurrent()) return
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
