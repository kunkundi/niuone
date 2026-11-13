<script setup>
import { computed } from 'vue'

const props = defineProps({
  config: {
    type: Object,
    required: true,
  },
  slug: {
    type: String,
    required: true,
  },
  modelStatus: {
    type: Object,
    default: () => ({}),
  },
  iwencaiStatus: {
    type: Object,
    default: () => ({}),
  },
})
const emit = defineEmits(['test-model', 'test-iwencai'])

const modelTests = computed(() => (
  Array.isArray(props.config.model_tests)
    ? props.config.model_tests.filter(test => test.group_slug === props.slug)
    : []
))
const iwencaiTest = computed(() => {
  const test = props.config.iwencai_test || {}
  if (test.group_slug === props.slug) return test
  return null
})
const reasoningCapabilities = computed(() => (
  Array.isArray(props.config.reasoning_effort_capabilities)
    ? props.config.reasoning_effort_capabilities
    : []
))

function effortMappings(capability) {
  const mappings = capability.mappings || {}
  return Object.entries(mappings)
    .map(([source, target]) => `${source} → ${target}`)
    .join('，') || '无'
}
</script>

<template>
  <section v-if="modelTests.length" class="model-test-panel" aria-label="模型连通性测试">
    <div class="model-test-panel-head">
      <div>
        <div class="model-test-panel-title">模型连通性测试</div>
        <div class="model-test-panel-note">测试页面当前填写值（包括流式模式和思考强度），不会自动保存；成功仅表示网关接受当前请求配置。API Key 留空时安全复用已保存密钥。</div>
      </div>
    </div>
    <div class="model-test-list">
      <div v-for="test in modelTests" :key="test.id" class="model-test-row">
        <div class="model-test-copy">
          <div class="model-test-label">{{ test.label || '模型' }}</div>
          <div class="model-test-description">{{ test.description || '验证当前模型配置是否可用。' }}</div>
        </div>
        <div class="model-test-action">
          <button
            type="button"
            class="model-test-button"
            :class="modelStatus[test.id]?.state ? `is-${modelStatus[test.id].state}` : ''"
            :data-model-test="test.id"
            :aria-describedby="`model-test-status-${test.id}`"
            :disabled="modelStatus[test.id]?.state === 'busy'"
            @click.stop="emit('test-model', test.id)"
          >{{ modelStatus[test.id]?.state === 'busy' ? '测试中...' : '测试模型连接' }}</button>
          <div
            :id="`model-test-status-${test.id}`"
            class="model-test-status"
            :class="modelStatus[test.id]?.state ? `is-${modelStatus[test.id].state}` : ''"
            data-model-test-status
            role="status"
            aria-live="polite"
          >{{ modelStatus[test.id]?.message || '' }}</div>
        </div>
      </div>
    </div>
    <details v-if="reasoningCapabilities.length" class="reasoning-effort-reference">
      <summary>查看常见模型思考强度表</summary>
      <div class="reasoning-effort-table-wrap">
        <table class="reasoning-effort-table">
          <thead>
            <tr>
              <th>模型</th>
              <th>允许填写</th>
              <th>兼容映射</th>
              <th>默认</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="capability in reasoningCapabilities" :key="capability.key">
              <td>
                <a :href="capability.source_url" target="_blank" rel="noopener noreferrer">{{ capability.models }}</a>
              </td>
              <td><code>{{ capability.accepted_efforts.join(', ') || '仅可留空' }}</code></td>
              <td>{{ effortMappings(capability) }}</td>
              <td><code>{{ capability.default_effort || '未注明' }}</code></td>
              <td>{{ capability.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="model-test-panel-note">能力表最近更新：{{ reasoningCapabilities.map(row => row.verified_on || '').sort().at(-1) }}。各型号按其核对记录维护；表外模型按自定义网关处理，不做固定白名单限制。</div>
    </details>
  </section>

  <section v-if="iwencaiTest" class="model-test-panel" aria-label="问财接口连通性测试">
    <div class="model-test-panel-head">
      <div>
        <div class="model-test-panel-title">问财接口连通性测试</div>
        <div class="model-test-panel-note">测试页面当前填写值，不会自动保存；API Key 留空时安全复用已保存密钥。</div>
      </div>
    </div>
    <div class="model-test-list">
      <div class="model-test-row">
        <div class="model-test-copy">
          <div class="model-test-label">{{ iwencaiTest.label || '问财接口' }}</div>
          <div class="model-test-description">{{ iwencaiTest.description || '验证问财行情接口；开启预检时同时验证三个消息面技能。' }}</div>
        </div>
        <div class="model-test-action">
          <button
            type="button"
            class="model-test-button"
            :class="iwencaiStatus.state ? `is-${iwencaiStatus.state}` : ''"
            data-iwencai-test
            aria-describedby="iwencai-test-status"
            :disabled="iwencaiStatus.state === 'busy'"
            @click.stop="emit('test-iwencai')"
          >{{ iwencaiStatus.state === 'busy' ? '测试中...' : '测试问财接口' }}</button>
          <div
            id="iwencai-test-status"
            class="model-test-status"
            :class="iwencaiStatus.state ? `is-${iwencaiStatus.state}` : ''"
            data-model-test-status
            role="status"
            aria-live="polite"
          >{{ iwencaiStatus.message || '' }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
