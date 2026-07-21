<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const title = ref('设置')

function handleTitle(event) {
  title.value = String(event.detail?.title || '设置')
}

onMounted(() => window.addEventListener('niuone:admin-title', handleTitle))
onBeforeUnmount(() => window.removeEventListener('niuone:admin-title', handleTitle))
</script>

<template>
  <h1 id="adminPageTitle">{{ title }}</h1>
</template>
