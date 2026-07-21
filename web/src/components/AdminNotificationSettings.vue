<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import AdminEnvInput from './AdminEnvInput.vue'

const props = defineProps({
  config: {
    type: Object,
    required: true,
  },
  items: {
    type: Array,
    required: true,
  },
  currentStates: {
    type: Object,
    required: true,
  },
  testStatus: {
    type: Object,
    default: () => ({}),
  },
})
const emit = defineEmits(['field-change', 'test-channel'])

function isTruthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase())
}

const root = ref(null)
const picker = ref('')
const channels = computed(() => props.config.notification_channels || [])
const byName = computed(() => Object.fromEntries(
  props.items.map(item => [String(item.name || ''), item]),
))
const generalItems = computed(() => (
  (props.config.notification_general_names || [])
    .map(name => byName.value[name])
    .filter(Boolean)
))
const fieldOverrides = reactive({})

function channelActive(channel) {
  const item = byName.value[channel.enabled_name] || {}
  return isTruthy(item.effective || item.file_value || '0')
}

function channelConfigured(channel) {
  const enabled = byName.value[channel.enabled_name] || {}
  return channelActive(channel)
    || String(enabled.file_value || '').trim() !== ''
    || (channel.field_names || []).some(name => {
      const item = byName.value[name] || {}
      const state = String(item.current_state || '').trim()
      return String(item.file_value || '').trim() !== ''
        || (state !== '' && state !== '未设置')
    })
}

const added = reactive(Object.fromEntries(
  (props.config.notification_channels || []).map(channel => [channel.id, channelConfigured(channel)]),
))
const active = reactive(Object.fromEntries(
  (props.config.notification_channels || []).map(channel => [channel.id, channelActive(channel)]),
))
const removed = reactive(Object.fromEntries(
  (props.config.notification_channels || []).map(channel => [channel.id, false]),
))
const selectedCount = computed(() => channels.value.filter(channel => added[channel.id]).length)

function fieldItem(name) {
  const item = byName.value[name] || { name, label: name, kind: 'text' }
  return fieldOverrides[name] ? { ...item, ...fieldOverrides[name] } : item
}

function currentState(name) {
  return String(props.currentStates[name] || '')
}

async function addChannel() {
  const channelId = picker.value
  if (!channelId || added[channelId]) return
  added[channelId] = true
  active[channelId] = true
  removed[channelId] = false
  picker.value = ''
  await nextTick()
  emit('field-change')
  root.value
    ?.querySelector(`[data-notification-channel-card="${channelId}"] [data-notification-channel-fields] input`)
    ?.focus()
}

async function toggleChannel(channelId) {
  active[channelId] = !active[channelId]
  await nextTick()
  emit('field-change')
}

async function removeChannel(channelId) {
  added[channelId] = false
  active[channelId] = false
  removed[channelId] = true
  await nextTick()
  emit('field-change')
  root.value?.querySelector('[data-notification-channel-picker]')?.focus()
}

function applySavedConfig(updatedConfig) {
  if (!Array.isArray(updatedConfig?.items)) return
  const notificationNames = new Set(props.items.map(item => String(item.name || '')))
  updatedConfig.items.forEach(item => {
    const name = String(item?.name || '')
    if (notificationNames.has(name)) fieldOverrides[name] = { ...item }
  })
}

defineExpose({ applySavedConfig })
</script>

<template>
  <div ref="root" class="notification-settings" data-notification-channels>
    <div class="notification-block">
      <div class="notification-block-head">
        <div>
          <div class="notification-block-title">基础设置</div>
          <div class="notification-block-note">总开关用于临时关闭全部渠道，不会删除任何渠道配置。</div>
        </div>
      </div>
      <div class="notification-general-grid">
        <div
          v-for="item in generalItems"
          :key="item.name"
          class="notification-compact-field"
          :data-notification-field="item.name"
        >
          <div class="notification-field-label">{{ item.label || item.name }}</div>
          <div><AdminEnvInput :item="fieldItem(item.name)" @field-change="emit('field-change')" /></div>
          <div class="config-meta">
            当前状态：<span
              :class="{'config-empty': !currentState(item.name)}"
              :data-env-current="item.name"
            >{{ currentState(item.name) || '未设置' }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="notification-block">
      <div class="notification-block-head">
        <div>
          <div class="notification-block-title">通知渠道</div>
          <div class="notification-block-note">每个渠道可单独启用或关闭；关闭会保留配置，移除并保存后才会清除配置。</div>
        </div>
      </div>
      <div class="notification-channel-add-row">
        <select v-model="picker" data-notification-channel-picker aria-label="选择通知渠道">
          <option value="">选择通知渠道</option>
          <option
            v-for="channel in channels"
            :key="channel.id"
            :value="channel.id"
            :hidden="added[channel.id]"
            :disabled="added[channel.id]"
          >{{ channel.label }}</option>
        </select>
        <button
          type="button"
          class="notification-channel-add"
          data-notification-channel-add
          :disabled="!picker"
          @click.stop="addChannel"
        >添加渠道</button>
      </div>
      <div v-if="!selectedCount" class="notification-channel-empty" data-notification-channel-empty>
        尚未添加通知渠道
      </div>
      <div class="notification-channel-grid" data-notification-channel-list>
        <article
          v-for="channel in channels"
          v-show="added[channel.id]"
          :key="channel.id"
          class="notification-channel-card"
          :data-notification-channel-card="channel.id"
          :data-notification-channel-added="added[channel.id] ? '1' : '0'"
          :data-notification-channel-active="String(Boolean(active[channel.id]))"
          :aria-hidden="String(!added[channel.id])"
        >
          <input
            type="hidden"
            :name="`env__${channel.enabled_name}`"
            :value="active[channel.id] ? '1' : '0'"
            :disabled="!added[channel.id]"
            data-notification-channel-enabled
          >
          <input
            type="hidden"
            :name="`notification_remove__${channel.id}`"
            :value="removed[channel.id] ? '1' : '0'"
            data-notification-channel-removed
          >
          <div class="notification-channel-card-head">
            <div>
              <div class="notification-channel-name" :id="`notification-channel-name-${channel.id}`">
                {{ channel.label }}
              </div>
              <div class="notification-channel-desc">{{ channel.description || '' }}</div>
            </div>
            <div class="notification-channel-head-actions">
              <div class="notification-channel-control">
                <button
                  type="button"
                  class="notification-channel-activation"
                  :class="{'is-active': active[channel.id]}"
                  data-notification-channel-activation
                  role="switch"
                  :aria-checked="String(Boolean(active[channel.id]))"
                  :aria-label="`${channel.label}渠道通知`"
                  @click.stop="toggleChannel(channel.id)"
                >
                  <span class="notification-channel-switch-track" aria-hidden="true">
                    <span class="notification-channel-switch-thumb" />
                  </span>
                  <span class="notification-channel-activation-state" data-notification-channel-activation-state>
                    {{ active[channel.id] ? '已启用' : '已关闭' }}
                  </span>
                </button>
              </div>
              <button
                type="button"
                class="notification-channel-remove"
                :data-notification-channel-remove="channel.id"
                @click.stop="removeChannel(channel.id)"
              >移除</button>
            </div>
          </div>
          <fieldset
            class="notification-channel-fields"
            data-notification-channel-fields
            :disabled="!added[channel.id]"
            :aria-labelledby="`notification-channel-name-${channel.id}`"
          >
            <div
              v-for="name in channel.field_names"
              :key="name"
              class="notification-field"
              :data-notification-field="name"
            >
              <div class="notification-field-label">{{ fieldItem(name).label || name }}</div>
              <div><AdminEnvInput :item="fieldItem(name)" @field-change="emit('field-change')" /></div>
              <div class="config-meta">
                当前状态：<span
                  :class="{'config-empty': !currentState(name)}"
                  :data-env-current="name"
                >{{ currentState(name) || '未设置' }}</span>
              </div>
            </div>
          </fieldset>
          <div class="notification-channel-actions">
            <button
              type="button"
              class="notification-channel-test"
              :class="testStatus[channel.id]?.state ? `is-${testStatus[channel.id].state}` : ''"
              :data-notification-channel-test="channel.id"
              :aria-describedby="`notification-test-status-${channel.id}`"
              :disabled="testStatus[channel.id]?.state === 'busy'"
              @click.stop="emit('test-channel', channel.id)"
            >{{ testStatus[channel.id]?.state === 'busy' ? '发送中...' : '发送测试通知' }}</button>
            <div class="notification-channel-test-copy">
              <span class="notification-channel-test-note">测试通知不受渠道开关影响</span>
              <span
                :id="`notification-test-status-${channel.id}`"
                class="notification-channel-test-status"
                :class="testStatus[channel.id]?.state ? `is-${testStatus[channel.id].state}` : ''"
                data-notification-channel-test-status
                role="status"
                aria-live="polite"
              >{{ testStatus[channel.id]?.message || '' }}</span>
            </div>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>
