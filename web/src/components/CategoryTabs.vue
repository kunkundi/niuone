<script setup>
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  dashboardCategoryFromLocation,
  dashboardCategoryPath,
  useDashboardTabs,
} from '../composables/useDashboardTabs.js'

const route = useRoute()
const router = useRouter()
const {
  activeCategory,
  categoryAvailable,
  initializeDashboardTabs,
  items,
  setActiveCategory,
} = useDashboardTabs()

watch(
  () => [route.path, route.query.category],
  ([path, queryCategory]) => setActiveCategory(dashboardCategoryFromLocation(path, String(queryCategory || ''))),
  { immediate: true },
)

async function selectCategory(category) {
  if (!categoryAvailable(category)) return
  if (category === activeCategory.value) return
  await router.push(dashboardCategoryPath(category))
}

onMounted(async () => {
  await initializeDashboardTabs()
  if (!categoryAvailable(activeCategory.value)) await router.replace('/practice')
})
</script>

<template>
  <nav id="categoryTabs" class="category-tabs" aria-label="主要栏目">
    <a
      v-for="item in items"
      :key="item.key"
      class="tab"
      :class="{ active: item.active }"
      :data-category="item.key"
      :href="item.href"
      :aria-current="item.active ? 'page' : undefined"
      @click.prevent="selectCategory(item.key)"
    >{{ item.label }}{{ item.count }}</a>
  </nav>
</template>
