import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const THEME_STORAGE_KEY = 'niuone-dashboard-theme-v1'

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY) || ''
    return value === 'light' || value === 'dark' ? value : ''
  } catch (error) {
    return ''
  }
}

function documentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

export function useTheme() {
  const theme = ref(documentTheme())
  const mediaQuery = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

  function applyTheme(nextTheme, persist = false) {
    const normalized = nextTheme === 'dark' ? 'dark' : 'light'
    theme.value = normalized
    document.documentElement.dataset.theme = normalized
    if (persist) {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, normalized)
      } catch (error) {
        // A blocked storage API must not prevent the visible theme change.
      }
    }
  }

  function toggleTheme() {
    applyTheme(theme.value === 'dark' ? 'light' : 'dark', true)
  }

  function handleSystemTheme(event) {
    if (!storedTheme()) applyTheme(event.matches ? 'dark' : 'light')
  }

  function handleStorage(event) {
    if (
      event.key === THEME_STORAGE_KEY
      && (event.newValue === 'light' || event.newValue === 'dark')
    ) {
      applyTheme(event.newValue)
    }
  }

  onMounted(() => {
    applyTheme(documentTheme())
    mediaQuery?.addEventListener?.('change', handleSystemTheme)
    window.addEventListener('storage', handleStorage)
  })

  onBeforeUnmount(() => {
    mediaQuery?.removeEventListener?.('change', handleSystemTheme)
    window.removeEventListener('storage', handleStorage)
  })

  const label = computed(() => (
    theme.value === 'dark' ? '切换为浅色主题' : '切换为深色主题'
  ))

  return {
    isDark: computed(() => theme.value === 'dark'),
    label,
    toggleTheme,
  }
}
