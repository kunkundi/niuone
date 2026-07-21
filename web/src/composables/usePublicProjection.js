const REFRESH_INTERVAL_MS = 15 * 1000
const REQUEST_TIMEOUT_MS = 15 * 1000

let revision = 0
let etag = ''
let sectionDigests = {}
let refreshTimer = 0
let requestController = null
let refreshRequest = null
const subscribers = new Set()

function snapshot() {
  return {
    revision,
    sectionDigests: { ...sectionDigests },
  }
}

function publish(nextSnapshot = snapshot()) {
  for (const subscriber of subscribers) {
    try {
      subscriber.onSnapshot(nextSnapshot)
    } catch (error) {
      console.error('public projection subscriber failed', error)
    }
  }
}

function publishError(error) {
  for (const subscriber of subscribers) {
    if (!subscriber.onError) continue
    try {
      subscriber.onError(error)
    } catch (subscriberError) {
      console.error('public projection error subscriber failed', subscriberError)
    }
  }
}

async function fetchJson(url, controller, options = {}) {
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
      ...options,
    })
    if (response.status === 304) return { notModified: true, response }
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return { payload: await response.json(), response }
  } catch (error) {
    if (timedOut) throw new Error('公开数据版本请求超时')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function refreshPublicProjection() {
  if (refreshRequest) return refreshRequest
  const controller = new AbortController()
  requestController = controller
  const request = (async () => {
    const headers = etag ? { 'If-None-Match': etag } : {}
    const latestResult = await fetchJson('/api/v2/public/latest', controller, { headers })
    if (latestResult.notModified) {
      const currentSnapshot = snapshot()
      publish(currentSnapshot)
      return currentSnapshot
    }
    const latest = latestResult.payload || {}
    const nextRevision = Number(latest.revision || 0)
    if (!Number.isInteger(nextRevision) || nextRevision < 1) throw new Error('公开数据版本无效')
    etag = latestResult.response.headers.get('ETag') || ''
    if (nextRevision === revision && Object.keys(sectionDigests).length) {
      const currentSnapshot = snapshot()
      publish(currentSnapshot)
      return currentSnapshot
    }
    const manifestPath = String(latest.manifest || '')
    if (!/^manifests\/[1-9][0-9]*\.json$/.test(manifestPath)) throw new Error('公开数据清单无效')
    const manifestResult = await fetchJson(`/api/v2/public/${manifestPath}`, controller, {
      cache: 'force-cache',
    })
    const nextDigests = {}
    for (const [name, reference] of Object.entries(manifestResult.payload?.sections || {})) {
      const digest = String(reference?.digest || '')
      if (/^[0-9a-f]{64}$/.test(digest)) nextDigests[name] = digest
    }
    if (!Object.keys(nextDigests).length) throw new Error('公开数据清单没有可用区块')
    revision = nextRevision
    sectionDigests = nextDigests
    const nextSnapshot = snapshot()
    publish(nextSnapshot)
    return nextSnapshot
  })().catch(error => {
    if (error?.name !== 'AbortError') publishError(error)
    return snapshot()
  }).finally(() => {
    if (requestController === controller) requestController = null
    if (refreshRequest === request) refreshRequest = null
  })
  refreshRequest = request
  return request
}

function startProjectionRefresh() {
  if (refreshTimer) return
  refreshPublicProjection()
  refreshTimer = window.setInterval(refreshPublicProjection, REFRESH_INTERVAL_MS)
}

function stopProjectionRefresh() {
  if (subscribers.size) return
  window.clearInterval(refreshTimer)
  refreshTimer = 0
  requestController?.abort()
  requestController = null
  refreshRequest = null
}

export function subscribePublicProjection(onSnapshot, onError = null) {
  const subscriber = { onSnapshot, onError }
  subscribers.add(subscriber)
  if (revision && Object.keys(sectionDigests).length) {
    queueMicrotask(() => {
      if (subscribers.has(subscriber)) onSnapshot(snapshot())
    })
  }
  startProjectionRefresh()
  return () => {
    subscribers.delete(subscriber)
    stopProjectionRefresh()
  }
}
