<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const state = ref('checking')
const value = ref('--')
const title = ref('正在检查 Docker Hub 最新版本')
let requestController = null

async function loadVersionStatus() {
  requestController = new AbortController()
  try {
    const response = await fetch('/api/version', {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: requestController.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const payload = await response.json()
    const current = String(payload.current_version || 'dev')
    const latest = payload.latest_version ? String(payload.latest_version) : ''
    const currentLabel = current === 'dev' ? '开发版' : current
    if (payload.check_ok !== true) {
      value.value = currentLabel
      state.value = 'error'
      title.value = `当前版本 ${currentLabel}；Docker Hub 最新版本检查失败`
    } else if (payload.update_available === true && latest) {
      value.value = `${currentLabel} → ${latest}`
      state.value = 'update'
      title.value = `发现新版本 ${latest}，点击查看 Docker Hub`
    } else if (payload.update_available === false) {
      value.value = currentLabel
      state.value = 'current'
      title.value = `当前版本 ${currentLabel}，已是最新版本`
    } else {
      value.value = latest ? `${currentLabel} · 最新 ${latest}` : currentLabel
      state.value = 'checking'
      title.value = latest
        ? `当前为${currentLabel}，Docker Hub 最新版本为 ${latest}`
        : `当前版本 ${currentLabel}`
    }
  } catch (error) {
    if (error.name === 'AbortError') return
    state.value = 'error'
    title.value = '版本信息加载失败，点击查看 Docker Hub'
    console.error('Version check failed', error)
  }
}

onMounted(loadVersionStatus)
onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <a
    id="versionStatus"
    class="version-status"
    :data-state="state"
    href="https://hub.docker.com/r/kunkundi/niuone"
    target="_blank"
    rel="noopener noreferrer"
    :title="title"
    :aria-label="title"
  >
    <span>版本</span><b id="versionValue">{{ value }}</b>
  </a>
</template>
