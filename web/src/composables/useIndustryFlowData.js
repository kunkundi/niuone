import { reactive } from 'vue'
import { configureIndustryFlowAnimation } from './useIndustryFlowAnimation.js'
import { useIndicesData } from './useIndicesData.js'

const REFRESH_INTERVAL_MS = 60 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const state = reactive({
  payload: { loading: true, loaded: false, available: false, nodes: [], links: [] },
})

let users = 0
let refreshTimer = 0
let requestController = null
let loadSequence = 0

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
    if (payload?.money_flow && (Array.isArray(payload.money_flow.inflow) || Array.isArray(payload.money_flow.outflow))) {
      useIndicesData().adoptMoneyFlow(payload.money_flow)
    }
    const hadData = state.payload.loaded === true
    state.payload = { ...(payload || {}), loading: false, loaded: true }
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
  } finally {
    window.clearTimeout(timeout)
    if (requestController === controller) requestController = null
  }
}

function activateIndustryFlow() {
  users += 1
  if (users > 1) return
  loadIndustryFlow({ background: state.payload.nodes?.length > 0 })
  refreshTimer = window.setInterval(() => loadIndustryFlow({ background: true }), REFRESH_INTERVAL_MS)
}

function deactivateIndustryFlow() {
  users = Math.max(0, users - 1)
  if (users) return
  window.clearInterval(refreshTimer)
  refreshTimer = 0
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
