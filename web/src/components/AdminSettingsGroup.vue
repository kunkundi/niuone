<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import AdminConnectionTests from './AdminConnectionTests.vue'
import AdminEnvInput from './AdminEnvInput.vue'
import AdminNotificationSettings from './AdminNotificationSettings.vue'

const props = defineProps({
  config: {
    type: Object,
    required: true,
  },
  slug: {
    type: String,
    required: true,
  },
})
const emit = defineEmits(['config-updated'])

function isTruthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase())
}

const group = computed(() => (
  (props.config.groups || []).find(entry => entry.slug === props.slug) || null
))
const items = computed(() => {
  if (!group.value) return []
  return (props.config.items || []).filter(item => String(item.group || '其他') === group.value.name)
})
const isNotificationGroup = computed(() => group.value?.name === '交易通知')
const itemCountLabel = computed(() => (
  isNotificationGroup.value
    ? `${(props.config.notification_channels || []).length} 个渠道`
    : `${items.value.length} 项`
))
const gatedNames = computed(() => new Set(props.config.ui?.us_feature_gated_names || []))
const strategyPreset = computed(() => String(props.config.ui?.strategy_preset_name || ''))
const initialUsToggle = (props.config.items || []).find(
  item => item.name === props.config.ui?.us_feature_toggle_name,
)
const initialStrategySource = items.value.find(
  item => ['strategy_source', 'strategy_suite'].includes(String(item.kind || '')),
)
const runtimeUsEnabled = ref(
  Boolean(initialUsToggle) && isTruthy(initialUsToggle.effective || initialUsToggle.file_value),
)
const runtimeStrategySource = ref(String(initialStrategySource?.file_value || 'zettaranc'))
const currentStates = reactive(Object.fromEntries(
  items.value.map(item => [item.name, String(item.current_state || '')]),
))
const form = ref(null)
const notificationSettings = ref(null)
const savedState = ref('1')
const saveResult = ref('')
const savePhase = ref('idle')
const saveStatus = ref('')
const savePressed = ref(false)
const editRevision = ref(0)
const modelStatus = reactive({})
const iwencaiStatus = reactive({ state: '', message: '' })
const notificationStatus = reactive({})
let savedSnapshot = ''
let saveResultTimer = 0
let pressedTimer = 0

const saveButtonText = computed(() => {
  if (savePhase.value === 'busy') return '保存中...'
  if (savePhase.value === 'ok') return '已保存'
  if (savePhase.value === 'error') return '保存失败'
  return '保存本组设置'
})

function formSnapshot() {
  if (!form.value) return ''
  const data = new FormData(form.value)
  form.value.querySelectorAll('input[type="password"][name]').forEach(input => {
    data.set(input.name, '')
  })
  return new URLSearchParams(data).toString()
}

function hasUnsavedSecret() {
  return Array.from(form.value?.querySelectorAll('input[type="password"]') || [])
    .some(input => input.value !== '')
}

function clearConnectionStatuses() {
  Object.keys(modelStatus).forEach(key => delete modelStatus[key])
  iwencaiStatus.state = ''
  iwencaiStatus.message = ''
  Object.keys(notificationStatus).forEach(key => delete notificationStatus[key])
}

function syncRuntimeToggles(target) {
  if (target?.matches?.('[data-feature-toggle="us"]')) {
    runtimeUsEnabled.value = target.value === '1'
  }
  if (target?.matches?.('[data-strategy-source-toggle]') && target.checked) {
    runtimeStrategySource.value = target.value
  }
}

function handleFormMutation(event) {
  syncRuntimeToggles(event?.target)
  editRevision.value += 1
  saveResult.value = ''
  clearConnectionStatuses()
  const matchesSaved = formSnapshot() === savedSnapshot && !hasUnsavedSecret()
  savedState.value = matchesSaved ? '1' : '0'
  if (savePhase.value !== 'busy') {
    savePhase.value = 'idle'
    saveStatus.value = matchesSaved ? '' : '有未保存修改'
  }
}

function rowHidden(item) {
  if (gatedNames.value.has(item.name) && !runtimeUsEnabled.value) return true
  if (item.name === strategyPreset.value && runtimeStrategySource.value !== 'preset_text') return true
  return false
}

function pulseSaveButton() {
  if (savedState.value === '1' || savePhase.value === 'busy') return
  savePressed.value = true
  window.clearTimeout(pressedTimer)
  pressedTimer = window.setTimeout(() => { savePressed.value = false }, 180)
}

function businessSaveMessage(payload) {
  if (!payload || payload.ok === false) return '保存失败'
  if (!payload.changed) return '配置未变化，无需重新应用'
  const count = Number(payload.changed_count || 0)
  const applied = ((payload.runtime && payload.runtime.applied) || [])
    .filter(item => item !== 'env')
  return `已保存 ${count} 项${applied.length ? `，已热应用：${applied.join('、')}` : ''}`
}

function applyConfigState(updatedConfig) {
  if (!updatedConfig || !Array.isArray(updatedConfig.items) || !form.value) return
  updatedConfig.items.forEach(item => {
    const name = String(item?.name || '')
    if (!name) return
    currentStates[name] = String(item.current_state || '')
    if (!item.secret) return
    const input = form.value.elements.namedItem(`env__${name}`)
    if (input && 'value' in input) {
      input.value = ''
      input.placeholder = String(item.file_state || '未设置')
    }
  })
}

async function save() {
  if (!form.value || savePhase.value === 'busy' || savedState.value === '1') return
  const submittedRevision = editRevision.value
  const submittedSnapshot = formSnapshot()
  savePhase.value = 'busy'
  saveStatus.value = '正在保存本组设置...'
  try {
    const response = await fetch(`/api/admin/config/env/${props.slug}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Accept': 'application/json',
        'X-NiuOne-Action': '1',
      },
      body: new URLSearchParams(new FormData(form.value)),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok === false) {
      throw new Error(payload?.error || '保存失败，请确认登录状态后重试')
    }
    if (payload.config && Array.isArray(payload.config.items)) {
      emit('config-updated', payload.config)
    }
    const formUnchanged = editRevision.value === submittedRevision
    if (formUnchanged) {
      applyConfigState(payload.config)
      notificationSettings.value?.applySavedConfig(payload.config)
    }
    if (payload.reauth_required) {
      savedSnapshot = formSnapshot()
      savedState.value = '1'
      window.location.replace('/admin')
      return
    }
    const message = businessSaveMessage(payload)
    if (formUnchanged) {
      savedSnapshot = formSnapshot()
      savedState.value = '1'
      savePhase.value = 'ok'
      saveStatus.value = message
      saveResult.value = 'ok'
      window.clearTimeout(saveResultTimer)
      saveResultTimer = window.setTimeout(() => { saveResult.value = '' }, 1600)
    } else {
      savedSnapshot = submittedSnapshot
      savedState.value = '0'
      savePhase.value = 'idle'
      saveStatus.value = `${message}；保存期间有新的修改，请再次保存`
    }
  } catch (error) {
    savePhase.value = 'error'
    saveStatus.value = error instanceof Error ? error.message : '保存失败，请稍后重试'
  }
}

function formFieldValue(name) {
  const field = form.value?.elements.namedItem(`env__${name}`)
  return field && 'value' in field ? String(field.value || '').trim() : ''
}

async function runModelTest(targetId) {
  if (modelStatus[targetId]?.state === 'busy') return
  const test = (props.config.model_tests || []).find(item => item.id === targetId)
  const body = new URLSearchParams({ target: targetId })
  ;(test?.field_names || []).forEach(name => body.set(`env__${name}`, formFieldValue(name)))
  modelStatus[targetId] = { state: 'busy', message: '正在连接模型...' }
  try {
    const response = await fetch('/api/admin/models/test', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Accept': 'application/json',
        'X-NiuOne-Action': '1',
      },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok !== true) {
      let message = payload?.error
      if (message === 'rate_limited') message = '测试过于频繁，请稍后重试'
      throw new Error(message || '模型连接失败，请确认配置后重试')
    }
    modelStatus[targetId] = { state: 'ok', message: payload.message || '模型已接通' }
  } catch (error) {
    modelStatus[targetId] = {
      state: 'error',
      message: error instanceof Error ? error.message : '模型连接失败',
    }
  }
}

async function runIwencaiTest() {
  if (iwencaiStatus.state === 'busy') return
  const body = new URLSearchParams()
  ;(props.config.iwencai_test?.field_names || []).forEach(name => {
    body.set(`env__${name}`, formFieldValue(name))
  })
  iwencaiStatus.state = 'busy'
  iwencaiStatus.message = '正在连接问财接口...'
  try {
    const response = await fetch('/api/admin/iwencai/test', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Accept': 'application/json',
        'X-NiuOne-Action': '1',
      },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok !== true) {
      let message = payload?.error
      if (message === 'rate_limited') message = '测试过于频繁，请稍后重试'
      throw new Error(message || '问财接口连接失败，请确认配置后重试')
    }
    iwencaiStatus.state = 'ok'
    iwencaiStatus.message = payload.message || '问财接口已接通'
  } catch (error) {
    iwencaiStatus.state = 'error'
    iwencaiStatus.message = error instanceof Error ? error.message : '问财接口连接失败'
  }
}

async function runNotificationTest(channelId) {
  if (notificationStatus[channelId]?.state === 'busy') return
  const channel = (props.config.notification_channels || []).find(item => item.id === channelId)
  const body = new URLSearchParams({ channel: channelId })
  ;(channel?.field_names || []).forEach(name => {
    body.set(`env__${name}`, formFieldValue(name))
  })
  body.set(
    'env__DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS',
    formFieldValue('DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS'),
  )
  notificationStatus[channelId] = { state: 'busy', message: '正在验证并发送...' }
  try {
    const response = await fetch('/api/admin/notifications/test', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Accept': 'application/json',
        'X-NiuOne-Action': '1',
      },
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok !== true) {
      let message = payload?.error
      if (message === 'rate_limited') message = '测试过于频繁，请稍后重试'
      throw new Error(message || '测试通知发送失败，请确认配置后重试')
    }
    notificationStatus[channelId] = {
      state: 'ok',
      message: payload.message || '测试通知已发送',
    }
  } catch (error) {
    notificationStatus[channelId] = {
      state: 'error',
      message: error instanceof Error ? error.message : '测试通知发送失败',
    }
  }
}

function handleBeforeUnload(event) {
  if (savedState.value !== '0') return
  event.preventDefault()
  event.returnValue = ''
}

function handleShortcut(event) {
  if (String(event.key || '').toLowerCase() !== 's'
    || (!event.metaKey && !event.ctrlKey)
    || event.altKey
    || savedState.value !== '0'
    || savePhase.value === 'busy') return
  event.preventDefault()
  save()
}

function confirmDiscardChanges() {
  return (
  savedState.value !== '0'
    || window.confirm('当前分组有未保存修改，确定离开吗？')
  )
}

onBeforeRouteLeave(confirmDiscardChanges)
onBeforeRouteUpdate(confirmDiscardChanges)

onMounted(async () => {
  await nextTick()
  savedSnapshot = formSnapshot()
  savedState.value = '1'
  window.addEventListener('beforeunload', handleBeforeUnload)
  window.addEventListener('keydown', handleShortcut)
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  window.removeEventListener('keydown', handleShortcut)
  window.clearTimeout(saveResultTimer)
  window.clearTimeout(pressedTimer)
})
</script>

<template>
  <div v-if="!group" class="errmsg">
    未找到该设置分组。<RouterLink class="toplink" to="/admin">返回全部设置</RouterLink>
  </div>
  <div v-else class="settings-detail">
    <nav class="settings-breadcrumbs" aria-label="设置导航">
      <RouterLink class="settings-back-link" to="/admin">
        <span aria-hidden="true">←</span><span>全部设置</span>
      </RouterLink>
    </nav>
    <form
      id="env-config-form"
      ref="form"
      class="settings-form"
      :data-settings-group="slug"
      :data-save-endpoint="`/api/admin/config/env/${slug}`"
      :data-saved-state="savedState"
      :data-save-result="saveResult || null"
      @submit.prevent.stop="save"
      @input.stop="handleFormMutation"
      @change.stop="handleFormMutation"
    >
      <input type="hidden" name="settings_group" :value="slug">
      <section class="settings-group" :id="`settings-${slug}`">
        <div class="settings-group-head">
          <div>
            <h2>{{ group.name }}</h2>
            <p v-if="group.note" class="settings-group-note">{{ group.note }}</p>
          </div>
          <span class="settings-count">{{ itemCountLabel }}</span>
        </div>
        <AdminNotificationSettings
          v-if="isNotificationGroup"
          ref="notificationSettings"
          :config="config"
          :items="items"
          :current-states="currentStates"
          :test-status="notificationStatus"
          @field-change="handleFormMutation"
          @test-channel="runNotificationTest"
        />
        <div v-else class="settings-list">
          <div
            v-for="item in items"
            :key="item.name"
            class="setting-row"
            :data-feature-gated="gatedNames.has(item.name) ? 'us' : null"
            :data-strategy-source-gated="item.name === strategyPreset ? 'preset_text' : null"
            :hidden="rowHidden(item)"
            :aria-hidden="String(rowHidden(item))"
          >
            <div class="setting-copy"><div class="config-label">{{ item.label || item.name }}</div></div>
            <div class="setting-editor">
              <AdminEnvInput :item="item" @field-change="handleFormMutation" />
            </div>
            <div class="setting-state">
              <div class="setting-state-item">
                <div class="setting-state-label">当前状态</div>
                <div
                  class="config-meta"
                  :class="{'config-empty': !currentStates[item.name]}"
                  :data-env-current="item.name"
                >{{ currentStates[item.name] || '未设置' }}</div>
              </div>
              <div class="setting-state-item">
                <div class="setting-state-label">默认</div>
                <div class="config-meta" :class="{'config-empty': !item.default}">{{ item.default || '未设置' }}</div>
              </div>
            </div>
          </div>
        </div>
        <AdminConnectionTests
          v-if="!isNotificationGroup"
          :config="config"
          :slug="slug"
          :model-status="modelStatus"
          :iwencai-status="iwencaiStatus"
          @test-model="runModelTest"
          @test-iwencai="runIwencaiTest"
        />
        <div class="settings-actions">
          <div class="settings-save-status" :class="savePhase" data-env-save-status role="status" aria-live="polite">
            {{ saveStatus }}
          </div>
          <button
            class="save-button"
            :class="{saved: savePhase === 'ok', error: savePhase === 'error', pressed: savePressed}"
            data-env-save-button
            type="submit"
            :disabled="savedState === '1' || savePhase === 'busy'"
            @pointerdown.stop="pulseSaveButton"
          >{{ saveButtonText }}</button>
        </div>
      </section>
    </form>
  </div>
</template>
