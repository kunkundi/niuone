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
  return test.group_slug === props.slug ? test : null
})
</script>

<template>
  <section v-if="modelTests.length" class="model-test-panel" aria-label="模型连通性测试">
    <div class="model-test-panel-head">
      <div>
        <div class="model-test-panel-title">模型连通性测试</div>
        <div class="model-test-panel-note">测试页面当前填写值，不会自动保存；API Key 留空时安全复用已保存密钥。</div>
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
          <div class="model-test-description">{{ iwencaiTest.description || '验证问财网关地址和 API Key。' }}</div>
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
