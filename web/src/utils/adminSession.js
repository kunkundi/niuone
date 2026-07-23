function responseError(payload, fallback) {
  return new Error(String(payload?.error || fallback))
}

export async function authenticateAdmin(credential) {
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
  return true
}
