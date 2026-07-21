import { ref, shallowRef } from 'vue'

function responseError(payload, fallback) {
  return new Error(String(payload?.error || fallback))
}

export function useAdminConfig() {
  const state = ref('loading')
  const config = shallowRef(null)
  const errorMessage = ref('')

  async function refresh() {
    state.value = 'loading'
    errorMessage.value = ''
    try {
      const response = await fetch('/api/admin/config', {
        credentials: 'same-origin',
        cache: 'no-store',
      })
      if (response.status === 403) {
        config.value = null
        state.value = 'login'
        return false
      }
      const payload = await response.json().catch(() => null)
      if (!response.ok || !payload || !Array.isArray(payload.items)) {
        throw responseError(payload, '设置加载失败')
      }
      config.value = payload
      state.value = 'ready'
      return true
    } catch (error) {
      config.value = null
      errorMessage.value = error instanceof Error ? error.message : '设置加载失败'
      state.value = 'error'
      return false
    }
  }

  async function authenticate(credential) {
    const body = new URLSearchParams()
    body.set('admin_password', String(credential || ''))
    const response = await fetch('/api/admin/session', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
      body,
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok !== true) {
      throw responseError(payload, '管理员凭据错误')
    }
    return refresh()
  }

  return {
    state,
    config,
    errorMessage,
    refresh,
    authenticate,
  }
}
