import { reactive } from 'vue'
import { subscribePublicProjection } from './usePublicProjection.js'

const CACHE_TTL_MS = 30 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const CACHE_KEY = 'niuniu-dashboard-practice-candidates-v1'
const CANDIDATES_SECTION = 'candidates'

const state = reactive({
  loading: true,
  loaded: false,
  items: [],
  count: 0,
  generatedAt: '',
  strategyMeta: {},
  strategyDistribution: {},
  running: false,
  startedAt: '',
  error: '',
})

let users = 0
let candidatesController = null
let loadSequence = 0
let unsubscribeProjection = null
let candidatesDigest = ''
let pendingCandidatesDigest = ''

function publishStrategyMeta() {
  window.__niuonePracticeStrategyMeta = state.strategyMeta
  window.dispatchEvent(new CustomEvent('niuone:practice-strategy-meta', {
    detail: { strategyMeta: state.strategyMeta },
  }))
}

function publishLastUpdated() {
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(state.generatedAt || '').slice(11, 19) || '--' },
  }))
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      items: state.items,
      count: state.count,
      generatedAt: state.generatedAt,
      strategyMeta: state.strategyMeta,
      strategyDistribution: state.strategyDistribution,
      running: state.running,
      startedAt: state.startedAt,
      candidatesDigest,
      savedAt: Date.now(),
    }))
  } catch {}
}

function restoreCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || '{}')
    if (!cached.savedAt || Date.now() - Number(cached.savedAt) > CACHE_TTL_MS) return
    state.items = Array.isArray(cached.items) ? cached.items : []
    state.count = Number(cached.count || state.items.length)
    state.generatedAt = String(cached.generatedAt || '')
    state.strategyMeta = cached.strategyMeta || {}
    state.strategyDistribution = cached.strategyDistribution || {}
    state.running = cached.running === true
    state.startedAt = String(cached.startedAt || '')
    state.loading = false
    state.loaded = true
    candidatesDigest = String(cached.candidatesDigest || '')
    publishStrategyMeta()
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
    if (timedOut) throw new Error('候选股请求超时')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

function applyCandidates(payload) {
  const items = Array.isArray(payload?.items)
    ? payload.items
    : (Array.isArray(payload?.candidates) ? payload.candidates : [])
  state.items = items
  state.count = Number(payload?.count || items.length)
  state.generatedAt = String(payload?.generated_at || '')
  state.strategyMeta = payload?.strategy_meta || {}
  state.strategyDistribution = payload?.strategy_distribution || {}
  state.running = payload?.running === true
  state.startedAt = String(payload?.started_at || '')
  state.error = String(payload?.error || '')
  state.loading = false
  state.loaded = true
  publishStrategyMeta()
  publishLastUpdated()
}

async function loadCandidates({ background = false } = {}) {
  const sequence = ++loadSequence
  candidatesController?.abort()
  const controller = new AbortController()
  candidatesController = controller
  if (!background || !state.items.length) state.loading = true
  try {
    const payload = await fetchJson('/api/practice_candidates', controller)
    if (sequence !== loadSequence) return false
    applyCandidates(payload || {})
    return true
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return false
    state.error = String(error?.message || error)
    state.loading = false
    state.loaded = true
    return false
  } finally {
    if (candidatesController === controller) candidatesController = null
  }
}

async function syncCandidates({ background = state.items.length > 0 } = {}) {
  if (candidatesController) return false
  const loaded = await loadCandidates({ background })
  if (!loaded) return false
  if (pendingCandidatesDigest) {
    candidatesDigest = pendingCandidatesDigest
    pendingCandidatesDigest = ''
  }
  saveCache()
  return true
}

function handleProjection(snapshot) {
  const digest = String(snapshot?.sectionDigests?.[CANDIDATES_SECTION] || '')
  if (!/^[0-9a-f]{64}$/.test(digest)) return
  if (!candidatesDigest && state.loaded && !pendingCandidatesDigest) {
    candidatesDigest = digest
    saveCache()
    return
  }
  if (digest === candidatesDigest && !pendingCandidatesDigest) return
  pendingCandidatesDigest = digest
  syncCandidates()
}

function activatePracticeCandidates() {
  users += 1
  if (users > 1) return
  unsubscribeProjection = subscribePublicProjection(handleProjection)
  if (state.loaded) {
    publishStrategyMeta()
    publishLastUpdated()
  } else {
    syncCandidates({ background: false })
  }
}

function deactivatePracticeCandidates() {
  users = Math.max(0, users - 1)
  if (users) return
  loadSequence += 1
  candidatesController?.abort()
  candidatesController = null
  unsubscribeProjection?.()
  unsubscribeProjection = null
}

restoreCache()

export function usePracticeCandidatesData() {
  return {
    state,
    activatePracticeCandidates,
    deactivatePracticeCandidates,
    refreshPracticeCandidates: syncCandidates,
  }
}
