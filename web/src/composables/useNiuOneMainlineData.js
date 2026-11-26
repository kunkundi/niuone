import { reactive } from 'vue'
import { subscribePublicProjection } from './usePublicProjection.js'

const CACHE_TTL_MS = 30 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000
const CACHE_KEY = 'niuniu-dashboard-mainline-v3'
const PROJECTION_SECTION = 'niuone_mainline'

const state = reactive({
  payload: {},
  loading: true,
  loaded: false,
  error: '',
})

let users = 0
let requestController = null
let loadSequence = 0
let unsubscribeProjection = null
let sectionDigest = ''
let pendingDigest = ''

function publishLastUpdated() {
  const generatedAt = String(state.payload?.generated_at || '')
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: generatedAt.slice(11, 19) || '--' },
  }))
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      payload: state.payload,
      sectionDigest,
      savedAt: Date.now(),
    }))
  } catch {}
}

function restoreCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || '{}')
    if (!cached.savedAt || Date.now() - Number(cached.savedAt) > CACHE_TTL_MS) return
    state.payload = cached.payload && typeof cached.payload === 'object' ? cached.payload : {}
    state.loading = false
    state.loaded = true
    sectionDigest = String(cached.sectionDigest || '')
  } catch {}
}

async function fetchMainline(controller, { manual = false } = {}) {
  let timedOut = false
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(manual ? '/api/niuone/mainline/refresh' : '/api/niuone/mainline', {
      method: manual ? 'POST' : 'GET',
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: manual ? { 'X-NiuOne-Action': '1' } : {},
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      const error = new Error(payload?.error || `HTTP ${response.status}`)
      error.code = String(payload?.error || '')
      error.status = response.status
      throw error
    }
    return payload
  } catch (error) {
    if (timedOut) throw new Error('题材强度请求超时')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

async function loadMainline({ background = false, manual = false } = {}) {
  const sequence = ++loadSequence
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  if (!background || !state.payload?.available) state.loading = true
  try {
    const payload = await fetchMainline(controller, { manual })
    if (sequence !== loadSequence) return false
    state.payload = payload && typeof payload === 'object' ? payload : {}
    state.error = ''
    state.loading = false
    state.loaded = true
    publishLastUpdated()
    return true
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return false
    if (error?.status === 403 && error?.code === 'admin_password_required') {
      state.error = ''
      state.loading = false
      state.loaded = true
      return 'admin_password_required'
    }
    state.error = String(error?.message || error)
    state.loading = false
    state.loaded = true
    return false
  } finally {
    if (requestController === controller) requestController = null
  }
}

async function syncMainline({
  background = state.payload?.available === true,
  manual = false,
} = {}) {
  if (requestController) return false
  const loaded = await loadMainline({ background, manual })
  if (loaded !== true) return loaded
  if (pendingDigest) {
    sectionDigest = pendingDigest
    pendingDigest = ''
  }
  saveCache()
  return true
}

function handleProjection(snapshot) {
  const digest = String(snapshot?.sectionDigests?.[PROJECTION_SECTION] || '')
  if (!/^[0-9a-f]{64}$/.test(digest)) return
  if (!sectionDigest && state.loaded && !pendingDigest) {
    sectionDigest = digest
    saveCache()
    return
  }
  if (digest === sectionDigest && !pendingDigest) return
  pendingDigest = digest
  syncMainline()
}

function activateNiuOneMainline() {
  users += 1
  if (users > 1) return
  unsubscribeProjection = subscribePublicProjection(handleProjection)
  if (state.loaded) publishLastUpdated()
  else syncMainline({ background: false })
}

function deactivateNiuOneMainline() {
  users = Math.max(0, users - 1)
  if (users) return
  loadSequence += 1
  requestController?.abort()
  requestController = null
  unsubscribeProjection?.()
  unsubscribeProjection = null
}

restoreCache()

export function useNiuOneMainlineData() {
  return {
    state,
    activateNiuOneMainline,
    deactivateNiuOneMainline,
    refreshNiuOneMainline: () => syncMainline({ background: false, manual: true }),
  }
}
