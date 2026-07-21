import { reactive } from 'vue'
import { subscribePublicProjection } from './usePublicProjection.js'
import {
  isUsablePracticePayload,
  mergePracticePayloadSnapshots,
} from '../utils/practicePayload.js'

const CACHE_TTL_MS = 30 * 1000
const REQUEST_TIMEOUT_MS = 20 * 1000
const FULL_HISTORY_RETRY_MS = 5 * 60 * 1000
const CACHE_KEY = 'niuniu-dashboard-practice-account-v1'
const FAST_SECTIONS = ['metadata', 'account', 'history', 'activity']

const state = reactive({
  loading: true,
  loaded: false,
  error: '',
  practice: {
    positions: [],
    equity_history: [],
    daily_equity_history: [],
    trade_log: [],
    decision_log: [],
    cash: 1_000_000,
    total_equity: 1_000_000,
  },
  benchmarks: { items: [] },
  manualCycle: { running: false, stage: 'idle', stage_label: '', error: '' },
  marketSummary: { loading: true, available: false, scan_count: 0 },
  marketSummaryGenerating: false,
  fullSnapshotStatus: 'idle',
})

let users = 0
let loadSequence = 0
let unsubscribeProjection = null
let manualPollTimer = 0
let fullSnapshotRequest = null
let fullSnapshotLastAttemptAt = 0
const controllers = new Map()
const observedDigests = {}

function publishLastUpdated() {
  const value = state.practice.current_time
    || state.practice.source_updated_at
    || state.practice.generated_at
    || ''
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(value).slice(11, 19) || '--' },
  }))
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      practice: state.practice,
      benchmarks: state.benchmarks,
      marketSummary: state.marketSummary,
      fullSnapshotStatus: state.fullSnapshotStatus,
      savedAt: Date.now(),
    }))
  } catch {}
}

function restoreCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || '{}')
    if (!cached.savedAt || Date.now() - Number(cached.savedAt) > CACHE_TTL_MS) return
    if (isUsablePracticePayload(cached.practice)) {
      state.practice = { ...cached.practice }
      delete state.practice.decision_model
      delete state.practice.decision_provider
      state.loading = false
      state.loaded = true
    }
    state.benchmarks = cached.benchmarks || state.benchmarks
    state.marketSummary = cached.marketSummary || state.marketSummary
    state.fullSnapshotStatus = cached.fullSnapshotStatus === 'loaded' ? 'loaded' : 'idle'
  } catch {}
}

function controllerFor(key) {
  controllers.get(key)?.abort()
  const controller = new AbortController()
  controllers.set(key, controller)
  return controller
}

async function fetchJson(url, { key, method = 'GET', action = false, cache = 'no-store' } = {}) {
  const controller = controllerFor(key || url)
  let timedOut = false
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      method,
      signal: controller.signal,
      credentials: 'same-origin',
      cache,
      headers: action ? { 'X-NiuOne-Action': '1' } : {},
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.error || `HTTP ${response.status}`)
    }
    return payload
  } catch (error) {
    if (timedOut) throw new Error('模拟账户请求超时')
    throw error
  } finally {
    window.clearTimeout(timeout)
    if (controllers.get(key || url) === controller) controllers.delete(key || url)
  }
}

async function loadFastPractice({ background = false } = {}) {
  const sequence = ++loadSequence
  if (!background && !state.loaded) state.loading = true
  try {
    const payload = await fetchJson('/api/niuniu_practice?fast=1&calendar_schema=1', {
      key: 'practice-fast',
      cache: 'no-cache',
    })
    if (sequence !== loadSequence || !isUsablePracticePayload(payload)) return false
    state.practice = mergePracticePayloadSnapshots(state.practice, payload)
    state.loading = false
    state.loaded = true
    state.error = ''
    publishLastUpdated()
    saveCache()
    return true
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return false
    state.loading = false
    state.loaded = true
    state.error = String(error?.message || error)
    return false
  }
}

async function loadBenchmarks() {
  try {
    state.benchmarks = await fetchJson('/api/practice_benchmarks', { key: 'benchmarks' })
    saveCache()
    return true
  } catch (error) {
    if (error?.name !== 'AbortError') {
      state.benchmarks = { ...state.benchmarks, error: String(error?.message || error) }
    }
    return false
  }
}

async function loadMarketSummary() {
  try {
    const payload = await fetchJson('/api/niuniu_practice/market-summary', {
      key: 'market-summary',
    })
    state.marketSummary = { ...(payload || {}), loading: false }
    saveCache()
    return true
  } catch (error) {
    if (error?.name !== 'AbortError') {
      state.marketSummary = {
        ...state.marketSummary,
        loading: false,
        error: String(error?.message || error),
      }
    }
    return false
  }
}

function scheduleManualCyclePoll() {
  if (manualPollTimer || !users) return
  manualPollTimer = window.setTimeout(async () => {
    manualPollTimer = 0
    try {
      const previousRunning = state.manualCycle.running === true
      state.manualCycle = await fetchJson('/api/niuniu_practice/manual-cycle', {
        key: 'manual-cycle-status',
      })
      if (state.manualCycle.running) scheduleManualCyclePoll()
      else if (previousRunning) await loadFastPractice({ background: true })
    } catch (error) {
      if (error?.name !== 'AbortError' && state.manualCycle.running) scheduleManualCyclePoll()
    }
  }, 1500)
}

async function loadManualCycleStatus() {
  try {
    state.manualCycle = await fetchJson('/api/niuniu_practice/manual-cycle', {
      key: 'manual-cycle-status',
    })
    if (state.manualCycle.running) scheduleManualCyclePoll()
    return true
  } catch (error) {
    if (error?.name !== 'AbortError') {
      state.manualCycle = { ...state.manualCycle, error: String(error?.message || error) }
    }
    return false
  }
}

async function ensureFullSnapshot() {
  if (state.fullSnapshotStatus === 'loaded') return true
  if (fullSnapshotRequest) return fullSnapshotRequest
  if (Date.now() - fullSnapshotLastAttemptAt < FULL_HISTORY_RETRY_MS) return false
  fullSnapshotLastAttemptAt = Date.now()
  state.fullSnapshotStatus = 'loading'
  const request = fetchJson('/api/niuniu_practice?snapshot_schema=2', {
    key: 'practice-full',
    cache: 'no-cache',
  }).then(payload => {
    if (!isUsablePracticePayload(payload)) throw new Error('完整模拟账户快照无效')
    state.practice = mergePracticePayloadSnapshots(state.practice, payload)
    state.fullSnapshotStatus = 'loaded'
    state.error = ''
    saveCache()
    return true
  }).catch(error => {
    if (error?.name !== 'AbortError') {
      state.fullSnapshotStatus = 'error'
      state.error = String(error?.message || error)
    } else {
      state.fullSnapshotStatus = 'idle'
    }
    return false
  }).finally(() => {
    if (fullSnapshotRequest === request) fullSnapshotRequest = null
  })
  fullSnapshotRequest = request
  return request
}

function digestSignature(snapshot, names) {
  return names.map(name => String(snapshot?.sectionDigests?.[name] || '')).join(':')
}

function handleProjection(snapshot) {
  const groups = {
    fast: digestSignature(snapshot, FAST_SECTIONS),
    benchmarks: digestSignature(snapshot, ['benchmarks']),
    marketSummary: digestSignature(snapshot, ['market_summary']),
  }
  for (const [key, signature] of Object.entries(groups)) {
    if (!signature.replaceAll(':', '')) continue
    const previous = observedDigests[key]
    observedDigests[key] = signature
    if (!previous) continue
    if (previous === signature) continue
    if (key === 'fast') loadFastPractice({ background: true })
    else if (key === 'benchmarks') loadBenchmarks()
    else loadMarketSummary()
  }
}

async function triggerManualCycle() {
  if (state.manualCycle.running) return false
  state.manualCycle = {
    ...state.manualCycle,
    running: true,
    stage: 'starting',
    stage_label: '正在启动',
    error: '',
  }
  try {
    state.manualCycle = await fetchJson('/api/niuniu_practice/manual-cycle', {
      key: 'manual-cycle-action',
      method: 'POST',
      action: true,
    })
    scheduleManualCyclePoll()
    return true
  } catch (error) {
    state.manualCycle = {
      ...state.manualCycle,
      running: false,
      stage: 'error',
      stage_label: '启动失败',
      error: String(error?.message || error),
    }
    return false
  }
}

async function triggerMarketSummary() {
  if (state.marketSummaryGenerating) return false
  state.marketSummaryGenerating = true
  state.marketSummary = { ...state.marketSummary, error: '' }
  try {
    const payload = await fetchJson('/api/niuniu_practice/market-summary', {
      key: 'market-summary-action',
      method: 'POST',
      action: true,
    })
    state.marketSummary = { ...payload, loading: false, stale: false }
    saveCache()
    return true
  } catch (error) {
    state.marketSummary = {
      ...state.marketSummary,
      loading: false,
      error: String(error?.message || error),
    }
    return false
  } finally {
    state.marketSummaryGenerating = false
  }
}

async function resumeTrading() {
  try {
    const payload = await fetchJson('/api/niuniu_practice/resume', {
      key: 'resume-trading',
      method: 'POST',
      action: true,
    })
    if (payload.resumed) await loadFastPractice({ background: true })
    return payload.resumed === true
  } catch (error) {
    state.error = String(error?.message || error)
    return false
  }
}

function activatePractice() {
  users += 1
  if (users > 1) return
  unsubscribeProjection = subscribePublicProjection(handleProjection, error => {
    if (!state.loaded) state.error = String(error?.message || error)
  })
  if (state.loaded) publishLastUpdated()
  loadFastPractice({ background: state.loaded })
  loadBenchmarks()
  loadMarketSummary()
  loadManualCycleStatus()
}

function deactivatePractice() {
  users = Math.max(0, users - 1)
  if (users) return
  unsubscribeProjection?.()
  unsubscribeProjection = null
  window.clearTimeout(manualPollTimer)
  manualPollTimer = 0
  loadSequence += 1
  for (const controller of controllers.values()) controller.abort()
  controllers.clear()
  if (state.fullSnapshotStatus === 'loading') state.fullSnapshotStatus = 'idle'
  fullSnapshotRequest = null
}

restoreCache()

export function usePracticeData() {
  return {
    state,
    activatePractice,
    deactivatePractice,
    ensureFullSnapshot,
    loadFastPractice,
    resumeTrading,
    triggerManualCycle,
    triggerMarketSummary,
  }
}
