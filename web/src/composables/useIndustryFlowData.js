import { reactive } from 'vue'
import { configureIndustryFlowAnimation } from './useIndustryFlowAnimation.js'
import { useIndicesData } from './useIndicesData.js'
import { startVisiblePolling } from '../utils/visiblePolling.js'
import {
  hasIndustryFlowNodes,
  hasIndustryMoneyFlowRows,
  INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS,
  mergeIndustryFlowPayload,
} from '../utils/industryFlowData.js'

const REFRESH_INTERVAL_MS = 60 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const state = reactive({
  payload: { loading: true, loaded: false, available: false, nodes: [], links: [] },
})

let users = 0
let stopRefreshPolling = null
let requestController = null
let loadSequence = 0
let emptyRetryTimer = 0
let emptyRetryAttempt = 0

function clearEmptyRetry({ resetAttempt = false } = {}) {
  if (emptyRetryTimer) window.clearTimeout(emptyRetryTimer)
  emptyRetryTimer = 0
  if (resetAttempt) emptyRetryAttempt = 0
}

function scheduleEmptyRetry() {
  if (
    !users
    || emptyRetryTimer
    || emptyRetryAttempt >= INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS.length
  ) return
  const delay = INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS[emptyRetryAttempt]
  emptyRetryAttempt += 1
  emptyRetryTimer = window.setTimeout(() => {
    emptyRetryTimer = 0
    if (!users || document.visibilityState === 'hidden') return
    loadIndustryFlow({ background: hasIndustryFlowNodes(state.payload) })
  }, delay)
}

function publishLastUpdated(payload) {
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(payload.generated_at || '').slice(11, 19) || '--' },
  }))
}

async function loadIndustryFlow({ background = false } = {}) {
  const sequence = ++loadSequence
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  let timedOut = false
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)
  if (!background && !state.payload.loaded) state.payload = { ...state.payload, loading: true }
  try {
    const response = await fetch('/api/industry-flow?compact=1', {
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
    })
    if (!response.ok) throw new Error(`industry flow failed: ${response.status}`)
    const payload = await response.json()
    if (sequence !== loadSequence) return
    const hadData = hasIndustryFlowNodes(state.payload)
    const merged = mergeIndustryFlowPayload(state.payload, payload)
    state.payload = merged.payload
    if (!merged.receivedData) {
      scheduleEmptyRetry()
      return
    }
    clearEmptyRetry({ resetAttempt: true })
    if (hasIndustryMoneyFlowRows(payload)) useIndicesData().adoptMoneyFlow(payload.money_flow)
    configureIndustryFlowAnimation(state.payload, hadData)
    publishLastUpdated(state.payload)
  } catch (error) {
    if (sequence !== loadSequence) return
    if (error?.name === 'AbortError' && !timedOut) return
    state.payload = {
      ...state.payload,
      loading: false,
      loaded: true,
      error: timedOut ? '行业资金流请求超时' : String(error),
    }
    scheduleEmptyRetry()
  } finally {
    window.clearTimeout(timeout)
    if (requestController === controller) requestController = null
  }
}

function activateIndustryFlow() {
  users += 1
  if (users > 1) return
  clearEmptyRetry({ resetAttempt: true })
  loadIndustryFlow({ background: hasIndustryFlowNodes(state.payload) })
  stopRefreshPolling = startVisiblePolling(
    () => loadIndustryFlow({ background: true }),
    REFRESH_INTERVAL_MS,
  )
}

function deactivateIndustryFlow() {
  users = Math.max(0, users - 1)
  if (users) return
  stopRefreshPolling?.()
  stopRefreshPolling = null
  clearEmptyRetry({ resetAttempt: true })
  loadSequence += 1
  requestController?.abort()
  requestController = null
}

export function useIndustryFlowData() {
  return {
    state,
    activateIndustryFlow,
    deactivateIndustryFlow,
    refreshIndustryFlow: loadIndustryFlow,
  }
}
