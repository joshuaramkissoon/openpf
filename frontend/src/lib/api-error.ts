import { toast } from 'sonner'

// Mirrors backend app/services/t212_errors.py error codes.
export type ApiErrorCode =
  | 'insufficient_funds'
  | 'ip_restricted'
  | 'auth_failed'
  | 'rate_limited'
  | 'risk_blocked'
  | 'validation'
  | 'broker_error'
  | 'unknown'

export interface ParsedApiError {
  code: ApiErrorCode
  message: string
  meta?: Record<string, unknown>
}

interface DetailEnvelope {
  code?: string
  message?: string
  meta?: Record<string, unknown>
}

/**
 * Parse an axios error into a typed {code, message, meta}. Handles both the new
 * structured envelope (detail is an object) and legacy string-detail endpoints.
 */
export function parseApiError(error: unknown): ParsedApiError {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object') {
    const env = detail as DetailEnvelope
    return {
      code: (env.code as ApiErrorCode) ?? 'unknown',
      message: env.message ?? 'Request failed',
      meta: env.meta,
    }
  }
  if (typeof detail === 'string' && detail) {
    return { code: 'unknown', message: detail }
  }
  if (error instanceof Error) return { code: 'unknown', message: error.message }
  return { code: 'unknown', message: 'Unexpected error' }
}

const TITLE_BY_CODE: Record<ApiErrorCode, string> = {
  insufficient_funds: 'Insufficient funds',
  ip_restricted: 'Execution key blocked',
  auth_failed: 'Execution key rejected',
  rate_limited: 'Rate limited',
  risk_blocked: 'Blocked by risk guard',
  validation: 'Cannot execute',
  broker_error: 'Broker error',
  unknown: 'Request failed',
}

/** Show a sonner toast tailored to the error code. Returns the parsed error. */
export function toastApiError(error: unknown, fallbackTitle?: string): ParsedApiError {
  const parsed = parseApiError(error)
  const title = parsed.code === 'unknown' && fallbackTitle ? fallbackTitle : TITLE_BY_CODE[parsed.code]
  // IP-restriction is the one the user must act on (update T212 allowlist) — keep it sticky.
  const sticky = parsed.code === 'ip_restricted'
  if (parsed.code === 'rate_limited' || parsed.code === 'risk_blocked') {
    toast.warning(title, { description: parsed.message })
  } else {
    toast.error(title, { description: parsed.message, duration: sticky ? 12000 : undefined })
  }
  return parsed
}
