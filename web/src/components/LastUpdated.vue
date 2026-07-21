<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const value = ref('--')

function handleUpdate(event) {
  value.value = String(event.detail?.value || '--')
}

onMounted(() => window.addEventListener('niuone:last-updated', handleUpdate))
onBeforeUnmount(() => window.removeEventListener('niuone:last-updated', handleUpdate))
</script>

<template>
  <div class="refresh-pill" title="最后刷新">
    <span>最后刷新</span><b id="updated">{{ value }}</b>
  </div>
</template>
