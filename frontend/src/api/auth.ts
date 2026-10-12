import type { AxiosInstance } from 'axios'

// Optional API token (backend OPENPF_API_TOKEN). Stored per-browser; attached to
// every API request and websocket URL. A 401 fires UNAUTHORIZED_EVENT so the
// token gate can prompt for it.
const TOKEN_KEY = 'openpf:token'
export const UNAUTHORIZED_EVENT = 'openpf:unauthorized'

export function getApiToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setApiToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable — token lasts for this page only */
  }
}

export function withAuth<T extends AxiosInstance>(instance: T): T {
  instance.interceptors.request.use((config) => {
    const token = getApiToken()
    if (token) config.headers.set('Authorization', `Bearer ${token}`)
    return config
  })
  instance.interceptors.response.use(undefined, (error) => {
    if (error?.response?.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    return Promise.reject(error)
  })
  return instance
}

/** Append ?token= to a websocket URL (browsers can't set WS headers). */
export function withWsToken(url: string): string {
  const token = getApiToken()
  if (!token) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
}
