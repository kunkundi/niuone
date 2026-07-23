<script setup>
import { nextTick, onMounted, ref } from 'vue'

const props = defineProps({
  authenticate: {
    type: Function,
    required: true,
  },
})

const credential = ref('')
const errorMessage = ref('')
const submitting = ref(false)
const input = ref(null)

async function focusInput() {
  await nextTick()
  input.value?.focus()
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    await props.authenticate(credential.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '管理员凭据错误'
    credential.value = ''
    submitting.value = false
    await focusInput()
  }
}

onMounted(focusInput)
</script>

<template>
  <form class="admin-login-box" data-admin-login-form @submit.prevent="submit">
    <h2>设置页验证</h2>
    <div class="admin-login-sub">请输入管理员密码或本机管理员密钥后进入设置。</div>
    <div v-if="errorMessage" class="error">{{ errorMessage }}</div>
    <label for="adminCredential">管理员凭据</label>
    <input
      id="adminCredential"
      ref="input"
      v-model="credential"
      name="admin_password"
      type="password"
      autocomplete="current-password"
      required
      autofocus
      :disabled="submitting"
    >
    <button type="submit" :disabled="submitting">
      {{ submitting ? '验证中...' : '进入设置' }}
    </button>
  </form>
</template>
