const API_BASE = '/api'

function getToken() {
  return localStorage.getItem('token')
}

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  const token = options.token || getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const { token: _token, ...fetchOptions } = options
  const res = await fetch(url, { ...fetchOptions, headers })
  if (res.status === 401) {
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  get: (path, options = {}) => request(path, { method: 'GET', ...options }),
  post: (path, body, options = {}) => request(path, { method: 'POST', body: JSON.stringify(body), ...options }),
  delete: (path, body, options = {}) => request(path, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined, ...options }),
}
