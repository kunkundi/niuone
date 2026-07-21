<script setup>
import { computed, nextTick, ref } from 'vue'

const props = defineProps({
  item: {
    type: Object,
    required: true,
  },
})
const emit = defineEmits(['field-change'])

const name = computed(() => String(props.item.name || ''))
const label = computed(() => String(props.item.label || props.item.name || '设置项'))
const fieldName = computed(() => `env__${name.value}`)
const value = computed(() => String(props.item.file_value ?? ''))
const kind = computed(() => String(props.item.kind || 'text'))
const boolValue = computed(() => {
  const normalized = value.value.trim().toLowerCase()
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return '1'
  return normalized === '' ? '' : '0'
})
const boolNoDefault = computed(() => (
  name.value === 'DASHBOARD_US_FEATURES_ENABLED' || Boolean(props.item.bool_no_default)
))
const apiMode = computed(() => (
  ['auto', 'responses', 'chat'].includes(value.value) ? value.value : 'auto'
))
const playbackSpeed = computed(() => (
  ['0.5', '0.75', '1', '1.5', '2'].includes(value.value) ? value.value : '0.5'
))
const listValues = ref([
  ...(String(props.item.kind || '') === 'time_list'
    ? (props.item.time_values || [])
    : (props.item.handle_values || [])),
])
const listInputType = computed(() => kind.value === 'time_list' ? 'time' : 'text')
const listLabel = computed(() => kind.value === 'time_list' ? '时间点' : '作者')
const strategyOptions = computed(() => (
  kind.value === 'strategy_suite'
    ? (props.item.strategy_suite_options || [])
    : (props.item.strategy_source_options || [])
))
const selectedStrategies = computed(() => new Set(props.item.strategy_values || []))
const selectedUniverse = computed(() => new Set(props.item.stock_universe_values || []))
const textPreset = computed(() => kind.value === 'preset_strategy_text')
const textMaxChars = computed(() => Number(
  textPreset.value
    ? props.item.preset_strategy_max_chars
    : props.item.trade_discipline_max_chars,
) || 4000)

async function addListValue() {
  listValues.value.push('')
  await nextTick()
  emit('field-change')
}

async function removeListValue(index) {
  listValues.value.splice(index, 1)
  await nextTick()
  emit('field-change')
}
</script>

<template>
  <input
    v-if="item.secret"
    type="password"
    :name="fieldName"
    :aria-label="label"
    :placeholder="item.file_state || '未设置'"
    autocomplete="new-password"
  >

  <select
    v-else-if="kind === 'bool'"
    :name="fieldName"
    :aria-label="label"
    :value="boolValue"
    :data-feature-toggle="name === 'DASHBOARD_US_FEATURES_ENABLED' ? 'us' : null"
  >
    <option v-if="!boolNoDefault" value="">默认</option>
    <option value="1">启用</option>
    <option value="0">停用</option>
  </select>

  <template v-else-if="kind === 'api_mode'">
    <select :name="fieldName" :aria-label="label" :value="apiMode">
      <option value="auto">自动</option>
      <option value="responses">Responses API（搜索工具）</option>
      <option value="chat">Chat Completions（兼容模式）</option>
    </select>
    <div class="config-meta">自动模式下，Grok 4.5 使用 Responses API，其他模型保持 Chat Completions</div>
  </template>

  <template v-else-if="kind === 'playback_speed'">
    <select :name="fieldName" :aria-label="label" :value="playbackSpeed">
      <option v-for="speed in ['0.5', '0.75', '1', '1.5', '2']" :key="speed" :value="speed">
        {{ speed }}x
      </option>
    </select>
    <div class="config-meta">控制资金流页面首次播放和重播速度</div>
  </template>

  <template v-else-if="kind === 'cron_time' || kind === 'time'">
    <input type="time" :name="fieldName" :aria-label="label" :value="value">
    <div class="config-meta">
      北京时间<span v-if="kind === 'cron_time' && item.day_label"> · {{ item.day_label }}</span>
    </div>
  </template>

  <template v-else-if="kind === 'time_list' || kind === 'handle_list'">
    <div
      class="time-list-control"
      data-time-list
      :data-field-name="fieldName"
      :data-input-type="listInputType"
      :data-placeholder="kind === 'handle_list' ? 'handle' : ''"
      :data-input-label="label"
    >
      <input type="hidden" :name="fieldName" value="">
      <div class="time-list-grid" data-time-list-items>
        <div v-for="(entry, index) in listValues" :key="index" class="time-list-item">
          <input
            :type="listInputType"
            :name="fieldName"
            :aria-label="`${label} ${index + 1}`"
            v-model="listValues[index]"
            :placeholder="kind === 'handle_list' ? 'handle' : null"
            :autocapitalize="kind === 'handle_list' ? 'off' : null"
            :spellcheck="kind === 'handle_list' ? 'false' : null"
          >
          <button
            type="button"
            class="time-list-remove"
            data-time-list-remove
            :aria-label="`删除${listLabel}`"
            @click.stop="removeListValue(index)"
          >x</button>
        </div>
      </div>
      <button
        type="button"
        class="time-list-add"
        data-time-list-add
        :aria-label="`添加${listLabel}`"
        @click.stop="addListValue"
      >+</button>
    </div>
    <div class="config-meta">{{ kind === 'time_list' ? '北京时间' : 'X/Twitter handle' }}</div>
  </template>

  <template v-else-if="kind === 'stock_universe'">
    <div class="strategy-multi-control">
      <input type="hidden" :name="fieldName" value="">
      <label
        v-for="option in (item.stock_universe_options || [])"
        :key="option.id"
        class="strategy-option"
        :style="{'--strategy-color': option.color || '#94a3b8'}"
      >
        <input
          type="checkbox"
          :name="fieldName"
          :value="option.id"
          :checked="selectedUniverse.has(option.id)"
          :aria-label="`${label}：${option.label || option.id}`"
        >
        <span class="strategy-option-main">
          <span class="strategy-option-title"><span class="strategy-option-dot" />{{ option.label || option.id }}</span>
          <span class="strategy-option-desc">{{ option.desc || '' }}</span>
        </span>
      </label>
    </div>
    <div class="config-meta">至少选择一项；ST 为跨板块独立范围，卖出已有持仓不受此设置限制</div>
  </template>

  <template v-else-if="kind === 'strategy_source' || kind === 'strategy_suite'">
    <div class="strategy-multi-control">
      <label
        v-for="option in strategyOptions"
        :key="option.id"
        class="strategy-option"
        :style="{'--strategy-color': option.color || '#94a3b8'}"
      >
        <input
          type="radio"
          :name="fieldName"
          :value="option.id"
          :checked="value === option.id"
          :aria-label="`${label}：${option.label || option.id}`"
          data-strategy-source-toggle
        >
        <span class="strategy-option-main">
          <span class="strategy-option-title"><span class="strategy-option-dot" />{{ option.label || option.id }}</span>
          <span class="strategy-option-desc">{{ option.desc || '' }}</span>
        </span>
      </label>
    </div>
    <div class="config-meta">每轮只启用一套完整策略；候选、买入、卖出和仓位规则互不混用</div>
  </template>

  <template v-else-if="kind === 'preset_strategy_text' || kind === 'trade_discipline_text'">
    <textarea
      :class="textPreset ? 'preset-strategy-textarea' : 'trade-discipline-textarea'"
      :name="fieldName"
      :aria-label="label"
      :maxlength="textMaxChars"
      spellcheck="false"
      :placeholder="textPreset ? '例如：只做主线强趋势回踩，买入后跌破5日线离场。' : '留空时使用内置交易纪律'"
      :value="value"
    />
    <div class="config-meta">
      {{ textPreset ? '激活后由买卖决策模型优化为选股、买入、卖出和仓位规则' : '直接写入买卖决策模型 prompt 的“必须遵守”段' }}
    </div>
  </template>

  <template v-else-if="kind === 'strategy_multi' || kind === 'strategy_single'">
    <div class="strategy-multi-control">
      <input type="hidden" :name="fieldName" value="">
      <label
        v-for="option in (item.strategy_options || [])"
        :key="option.id"
        class="strategy-option"
        :style="{'--strategy-color': option.color || '#94a3b8'}"
      >
        <input
          :type="kind === 'strategy_single' ? 'radio' : 'checkbox'"
          :name="fieldName"
          :value="option.id"
          :checked="selectedStrategies.has(option.id)"
          :aria-label="`${label}：${option.label || option.id}`"
        >
        <span class="strategy-option-main">
          <span class="strategy-option-title"><span class="strategy-option-dot" />{{ option.label || option.id }}</span>
          <span class="strategy-option-desc">{{ option.desc || '' }}</span>
        </span>
      </label>
    </div>
    <div class="config-meta">每次只启用一个内置策略</div>
  </template>

  <template v-else-if="kind === 'context_length' || kind === 'max_tokens'">
    <input
      type="text"
      :name="fieldName"
      :aria-label="label"
      :value="value"
      :placeholder="kind === 'context_length' ? '默认 128000；例如 128K、1M 或 1000000' : '默认 4096；例如 2048 或 8192'"
      inputmode="numeric"
    >
    <div class="config-meta">
      {{ kind === 'context_length' ? '默认 128000 tokens；填写后保存为数字 tokens' : '默认 4096 tokens；按所选接口映射为兼容的输出长度参数' }}
    </div>
  </template>

  <input
    v-else
    :type="kind === 'int' ? 'number' : 'text'"
    :name="fieldName"
    :aria-label="label"
    :value="value"
    :min="kind === 'int' && item.min ? item.min : null"
    :max="kind === 'int' && item.max ? item.max : null"
  >
</template>
