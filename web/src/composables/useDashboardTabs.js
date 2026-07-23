import { computed, reactive, ref } from 'vue'

const CATEGORY_ORDER = ['practice', 'indices', 'market_monitor', 'dragon_tiger', 'x_monitor', 'us_ratings']
const CATEGORY_LABELS = {
  practice: '模拟交易',
  indices: '指数行情',
  market_monitor: '盘面监控',
  dragon_tiger: '龙虎榜',
  x_monitor: '推特监控',
  us_ratings: '美股机构买入评级',
}
const CATEGORY_PATHS = {
  practice: '/practice',
  indices: '/indices',
  industry_flow: '/industry-flow',
  market_monitor: '/market-monitor',
  dragon_tiger: '/dragon-tiger',
  x_monitor: '/x-monitor',
  us_ratings: '/us-ratings',
}
const PATH_CATEGORIES = Object.fromEntries(
  Object.entries(CATEGORY_PATHS).map(([category, path]) => [path, category]),
)
const LEGACY_CATEGORY_ALIASES = { b1_screen: 'practice' }
const US_FEATURE_CATEGORIES = new Set(['x_monitor', 'us_ratings'])
const MESSAGE_COUNT_CATEGORIES = ['market_monitor', 'x_monitor', 'us_ratings']
const REQUEST_TIMEOUT_MS = 15 * 1000

const initialQueryCategory = new URLSearchParams(window.location.search).get('category') || ''
const initialCategory = PATH_CATEGORIES[window.location.pathname]
  || LEGACY_CATEGORY_ALIASES[initialQueryCategory]
  || initialQueryCategory
  || 'practice'
const activeCategory = ref(Object.hasOwn(CATEGORY_PATHS, initialCategory) ? initialCategory : 'practice')
const usFeaturesEnabled = ref(false)
const bootstrapLoaded = ref(false)
const bootstrapError = ref('')
const countOverrides = reactive({
  market_monitor: '',
  x_monitor: '',
  us_ratings: '',
})
let bootstrapRequest = null

function categoryAvailable(category) {
  return !US_FEATURE_CATEGORIES.has(category) || usFeaturesEnabled.value
}

const items = computed(() => CATEGORY_ORDER
  .filter(categoryAvailable)
  .map(key => ({
    key,
    href: CATEGORY_PATHS[key],
    label: CATEGORY_LABELS[key],
    count: String(countOverrides[key] || ''),
    active: activeCategory.value === key || (activeCategory.value === 'industry_flow' && key === 'indices'),
  })))

export function dashboardCategoryFromLocation(path, queryCategory = '') {
  const category = PATH_CATEGORIES[path]
    || LEGACY_CATEGORY_ALIASES[queryCategory]
    || queryCategory
    || 'practice'
  return Object.hasOwn(CATEGORY_PATHS, category) ? category : 'practice'
}

export function dashboardCategoryPath(category) {
  return CATEGORY_PATHS[LEGACY_CATEGORY_ALIASES[category] || category] || CATEGORY_PATHS.practice
}

function setActiveCategory(category) {
  activeCategory.value = dashboardCategoryFromLocation(CATEGORY_PATHS[category] || '', category)
}

function setCategoryCount(category, count) {
  countOverrides[category] = String(count || '')
}

function applyBootstrapCounts(counts) {
  if (!counts || typeof counts !== 'object') return
  for (const category of MESSAGE_COUNT_CATEGORIES) {
    if (!Object.hasOwn(counts, category)) continue
    const count = Number(counts[category])
    if (!Number.isFinite(count)) continue
    setCategoryCount(category, ` · ${Math.max(0, Math.trunc(count))}`)
  }
}

async function initializeDashboardTabs() {
  if (bootstrapLoaded.value) return { usFeaturesEnabled: usFeaturesEnabled.value }
  if (bootstrapRequest) return bootstrapRequest
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const request = fetch('/api/dashboard/bootstrap', {
    credentials: 'same-origin',
    cache: 'no-store',
    signal: controller.signal,
  }).then(async response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const payload = await response.json()
    usFeaturesEnabled.value = payload.us_features_enabled === true
    applyBootstrapCounts(payload.message_counts)
    bootstrapError.value = ''
    bootstrapLoaded.value = true
    return { ...payload, usFeaturesEnabled: usFeaturesEnabled.value }
  }).catch(error => {
    if (error?.name === 'AbortError') bootstrapError.value = '栏目配置请求超时'
    else bootstrapError.value = String(error?.message || error)
    bootstrapLoaded.value = true
    return { usFeaturesEnabled: false, error: bootstrapError.value }
  }).finally(() => {
    window.clearTimeout(timeout)
    if (bootstrapRequest === request) bootstrapRequest = null
  })
  bootstrapRequest = request
  return request
}

export function useDashboardTabs() {
  return {
    activeCategory,
    bootstrapError,
    bootstrapLoaded,
    categoryAvailable,
    initializeDashboardTabs,
    items,
    setActiveCategory,
    setCategoryCount,
  }
}
