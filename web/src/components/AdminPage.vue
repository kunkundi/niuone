<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AdminLogin from './AdminLogin.vue'
import AdminPageTitle from './AdminPageTitle.vue'
import AdminSettingsGroup from './AdminSettingsGroup.vue'
import AdminSettingsIndex from './AdminSettingsIndex.vue'
import ThemeToggle from './ThemeToggle.vue'
import { useAdminConfig } from '../composables/useAdminConfig.js'

document.title = '牛牛1号'
const { state, config, errorMessage, refresh, authenticate } = useAdminConfig()
const route = useRoute()
const groupSlug = computed(() => String(route.params.group || ''))
const activeGroup = computed(() => (
  (config.value?.groups || []).find(group => group.slug === groupSlug.value) || null
))
let pendingConfig = null

function setTitle(title) {
  window.dispatchEvent(new CustomEvent('niuone:admin-title', { detail: { title } }))
}

watch(state, value => {
  if (value === 'login') setTitle('设置验证')
  else if (value === 'error') setTitle('设置加载失败')
})

watch([state, groupSlug], ([currentState, slug]) => {
  if (currentState !== 'ready' || !config.value) return
  if (!slug) {
    setTitle('设置')
    return
  }
  setTitle(activeGroup.value?.name || '设置分组不存在')
}, { flush: 'post' })

watch(groupSlug, () => {
  if (!pendingConfig) return
  config.value = pendingConfig
  pendingConfig = null
})

function acceptUpdatedConfig(updated) {
  if (!updated || !Array.isArray(updated.items)) return
  if (!groupSlug.value) config.value = updated
  else pendingConfig = updated
}

onMounted(() => {
  refresh()
})
</script>

<template>
  <header class="admin-header">
    <div class="admin-header-inner">
      <div><div class="eyebrow">牛牛1号 · 设置</div><AdminPageTitle /></div>
      <div id="adminHeaderActions" class="admin-header-actions">
        <ThemeToggle button-id="adminThemeToggle" button-class="admin-theme-toggle" />
        <a class="toplink" href="/">返回首页</a>
      </div>
    </div>
  </header>
  <main
    id="adminApp"
    class="admin-main"
    aria-live="polite"
    :aria-busy="state === 'loading' ? 'true' : 'false'"
  >
    <div v-if="state === 'loading'" class="admin-loading">设置加载中…</div>
    <AdminLogin v-else-if="state === 'login'" :authenticate="authenticate" />
    <div v-else-if="state === 'error'" class="errmsg">{{ errorMessage || '设置加载失败' }}</div>
    <AdminSettingsIndex
      v-else-if="state === 'ready' && !groupSlug && config"
      :config="config"
    />
    <AdminSettingsGroup
      v-else-if="state === 'ready' && groupSlug && config"
      :key="groupSlug"
      :config="config"
      :slug="groupSlug"
      @config-updated="acceptUpdatedConfig"
    />
  </main>
</template>

<style src="../../../frontend/admin.css"></style>
