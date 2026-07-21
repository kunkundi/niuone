<script setup>
import { useTheme } from '../composables/useTheme.js'

defineProps({
  buttonClass: {
    type: String,
    required: true,
  },
  buttonId: {
    type: String,
    required: true,
  },
})

const { isDark, label, toggleTheme } = useTheme()
</script>

<template>
  <button
    :id="buttonId"
    type="button"
    :class="buttonClass"
    :title="label"
    :aria-label="label"
    :aria-pressed="isDark ? 'true' : 'false'"
    @click="toggleTheme"
  >
    <span class="theme-icon theme-icon-moon" aria-hidden="true">
      <svg viewBox="0 0 24 24" focusable="false"><path d="M20.5 14.3A8.5 8.5 0 0 1 9.7 3.5a8.5 8.5 0 1 0 10.8 10.8Z" /></svg>
    </span>
    <span class="theme-icon theme-icon-sun" aria-hidden="true">
      <svg viewBox="0 0 24 24" focusable="false"><circle cx="12" cy="12" r="3.75" /><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" /></svg>
    </span>
  </button>
</template>
