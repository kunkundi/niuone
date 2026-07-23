import { computed, reactive } from 'vue'
import { formatNumber } from '../utils/marketDisplay.js'

const SPEED_OPTIONS = [0.5, 0.75, 1, 1.5, 2]
const SIDE_LIMIT = 10
const SAMPLE_PLAYBACK_MS = 460
const MIN_PLAYBACK_MS = 9000
const MAX_PLAYBACK_MS = 110000
const motionReduced = typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true

const animation = reactive({
  frame: 0,
  playing: !motionReduced,
  speed: 0.5,
  progress: motionReduced ? 1 : 0,
  lastTime: 0,
  seeking: false,
  wasPlaying: false,
  speedUserOverride: false,
})

export function timelineFrames(payload = {}) {
  return (Array.isArray(payload.timeline) ? payload.timeline : [])
    .filter(frame => frame && Array.isArray(frame.nodes) && frame.nodes.length)
    .sort((left, right) => String(left.generated_at || '').localeCompare(String(right.generated_at || '')))
}

function interpolateTime(leftTime, rightTime, ratio) {
  const parse = value => {
    const match = String(value || '').match(/^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})$/)
    if (!match) return null
    return {
      date: match[1],
      seconds: Number(match[2]) * 3600 + Number(match[3]) * 60 + Number(match[4]),
    }
  }
  const left = parse(leftTime)
  const right = parse(rightTime)
  if (!left || !right || left.date !== right.date) return ratio < 0.5 ? String(leftTime || '') : String(rightTime || '')
  const seconds = Math.round(left.seconds + (right.seconds - left.seconds) * ratio)
  const clock = [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
    .map(value => String(value).padStart(2, '0'))
    .join(':')
  return `${left.date} ${clock}`
}

function applyEqualChipVolumes(nodes) {
  if (!nodes.length) return nodes
  const scale = Math.max(8, ...nodes.map(node => Math.abs(Number(node.net_flow_yi || 0))))
  return nodes.map(node => {
    const net = Number(node.net_flow_yi || 0)
    const score = Math.max(-1, Math.min(1, net / scale))
    return {
      ...node,
      base_volume_yi: 1,
      volume_scale_yi: scale,
      volume_score: score,
      volume_yi: 1 + 0.8 * score,
      magnitude_yi: Math.abs(net),
    }
  })
}

export function configuredSideLimit(payload = {}) {
  const configured = Math.round(Number(payload.settings?.side_limit))
  return Number.isFinite(configured) ? Math.max(1, Math.min(SIDE_LIMIT, configured)) : SIDE_LIMIT
}

export function splitSortedNodes(nodes, sideLimit = SIDE_LIMIT) {
  const limit = Math.max(1, Math.min(SIDE_LIMIT, Math.round(Number(sideLimit) || SIDE_LIMIT)))
  const list = Array.isArray(nodes) ? nodes : []
  return {
    outflow: list
      .filter(node => Number(node.net_flow_yi || 0) < 0)
      .sort((left, right) => Math.abs(Number(right.net_flow_yi || 0)) - Math.abs(Number(left.net_flow_yi || 0)))
      .slice(0, limit),
    inflow: list
      .filter(node => Number(node.net_flow_yi || 0) > 0)
      .sort((left, right) => Number(right.net_flow_yi || 0) - Number(left.net_flow_yi || 0))
      .slice(0, limit),
  }
}

export function frameAt(payload, progress) {
  const frames = timelineFrames(payload)
  if (frames.length < 2) return null
  const clamped = Math.max(0, Math.min(1, Number(progress || 0)))
  const position = clamped * (frames.length - 1)
  const leftIndex = Math.min(frames.length - 1, Math.floor(position))
  const rightIndex = Math.min(frames.length - 1, leftIndex + 1)
  const ratio = Math.max(0, Math.min(1, position - leftIndex))
  const left = frames[leftIndex]
  const right = frames[rightIndex]
  const leftById = new Map(left.nodes.map(node => [node.id, node]))
  const rightById = new Map(right.nodes.map(node => [node.id, node]))
  const universe = new Map()
  for (const node of [...(payload.nodes || []), ...left.nodes, ...right.nodes]) {
    if (node?.id) universe.set(node.id, { ...(universe.get(node.id) || {}), ...node })
  }
  const nodes = applyEqualChipVolumes([...universe.values()].map(base => {
    const before = leftById.get(base.id) || { net_flow_yi: 0, inflow_yi: 0, outflow_yi: 0 }
    const after = rightById.get(base.id) || { net_flow_yi: 0, inflow_yi: 0, outflow_yi: 0 }
    const interpolate = key => Number(before[key] || 0) + (Number(after[key] || 0) - Number(before[key] || 0)) * ratio
    const netFlow = interpolate('net_flow_yi')
    return {
      ...base,
      role: Math.abs(netFlow) < 0.0001 ? base.role : (netFlow > 0 ? 'inflow' : 'outflow'),
      net_flow_yi: netFlow,
      magnitude_yi: Math.abs(netFlow),
      inflow_yi: interpolate('inflow_yi'),
      outflow_yi: interpolate('outflow_yi'),
    }
  }))
  const visible = splitSortedNodes(nodes, configuredSideLimit(payload))
  return {
    generated_at: interpolateTime(left.generated_at, right.generated_at, ratio),
    nodes: [...visible.outflow, ...visible.inflow],
  }
}

export function playbackDuration(frameCount) {
  const intervals = Math.max(1, Number(frameCount || 0) - 1)
  return Math.max(MIN_PLAYBACK_MS, Math.min(MAX_PLAYBACK_MS, intervals * SAMPLE_PLAYBACK_MS))
}

export function seekValueFromClientX(clientX, trackLeft, trackWidth) {
  const width = Number(trackWidth)
  const position = Number(clientX) - Number(trackLeft)
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(position)) return 0
  return Math.round(Math.max(0, Math.min(1, position / width)) * 1000)
}

export function configureIndustryFlowAnimation(payload, hadData) {
  const configuredSpeed = Number(payload?.settings?.playback_speed)
  if (!animation.speedUserOverride && SPEED_OPTIONS.includes(configuredSpeed)) animation.speed = configuredSpeed
  const frames = timelineFrames(payload)
  if (motionReduced || !hadData) {
    animation.progress = 1
    animation.playing = false
  } else if (frames.length > 1) {
    animation.progress = hadData ? Math.max(0, (frames.length - 2) / (frames.length - 1)) : 0
    animation.playing = true
  }
}

export function signedYi(value) {
  const raw = Number(value || 0)
  const number = Math.abs(raw) < 0.05 ? 0 : raw
  return `${number >= 0 ? '+' : ''}${formatNumber(number, 1)}亿`
}

export function barScale(value, maximum) {
  const maxValue = Math.max(1e-6, Number(maximum || 0))
  const magnitude = Math.max(0, Math.abs(Number(value || 0)))
  return Math.max(4, Math.min(100, magnitude / maxValue * 100)) / 100
}

export function useIndustryFlowAnimation(payloadRef) {
  const frames = computed(() => timelineFrames(payloadRef.value))
  const actualPlayback = computed(() => frames.value.length > 1)
  const currentFrame = computed(() => frameAt(payloadRef.value, animation.progress))
  const visibleNodes = computed(() => currentFrame.value?.nodes || payloadRef.value.nodes || [])
  const sides = computed(() => splitSortedNodes(visibleNodes.value, configuredSideLimit(payloadRef.value)))
  const maximum = computed(() => Math.max(1, ...visibleNodes.value.map(node => Math.abs(Number(node.net_flow_yi || 0))), 1))
  const displayedProgress = computed(() => actualPlayback.value
    ? Math.max(0, Math.min(1, animation.progress))
    : Math.min(1, Math.max(0, animation.progress) / 0.82))
  const currentTime = computed(() => String(currentFrame.value?.generated_at || frames.value[0]?.generated_at || '').slice(11, 19))

  function stop() {
    if (animation.frame) window.cancelAnimationFrame(animation.frame)
    animation.frame = 0
  }

  function start() {
    stop()
    animation.lastTime = performance.now()
    if (!animation.playing) return
    const tick = now => {
      if (!animation.playing) {
        animation.frame = 0
        return
      }
      const delta = Math.min(80, Math.max(0, now - animation.lastTime))
      animation.lastTime = now
      const baseDuration = actualPlayback.value ? playbackDuration(frames.value.length) : 7200
      const next = animation.progress + delta / (baseDuration / animation.speed)
      if (actualPlayback.value && next >= 1) {
        animation.progress = 1
        animation.playing = false
        animation.frame = 0
        return
      }
      animation.progress = actualPlayback.value ? next : next % 1
      animation.frame = window.requestAnimationFrame(tick)
    }
    animation.frame = window.requestAnimationFrame(tick)
  }

  function toggle() {
    animation.playing = !animation.playing
    if (animation.playing) {
      if (actualPlayback.value && animation.progress >= 1) animation.progress = 0
      start()
    } else stop()
  }

  function replay() {
    animation.progress = 0
    animation.playing = true
    start()
  }

  function setSpeed(value) {
    const speed = Number(value)
    if (!SPEED_OPTIONS.includes(speed)) return
    animation.speedUserOverride = true
    animation.speed = speed
    animation.lastTime = performance.now()
  }

  function beginSeek() {
    if (animation.seeking) return
    animation.seeking = true
    animation.wasPlaying = animation.playing
    animation.playing = false
    stop()
  }

  function seek(value) {
    const requested = Math.max(0, Math.min(1, Number(value || 0) / 1000))
    animation.progress = actualPlayback.value ? requested : requested * 0.82
    animation.lastTime = performance.now()
  }

  function endSeek() {
    if (!animation.seeking) return
    const resume = animation.wasPlaying
    animation.seeking = false
    animation.wasPlaying = false
    if (resume) {
      animation.playing = true
      start()
    }
  }

  return {
    animation,
    speedOptions: SPEED_OPTIONS,
    frames,
    actualPlayback,
    sides,
    maximum,
    displayedProgress,
    currentTime,
    start,
    stop,
    toggle,
    replay,
    setSpeed,
    beginSeek,
    seek,
    endSeek,
  }
}
