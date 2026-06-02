import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export type OrderAccount = 'invest' | 'stocks_isa'
export type OrderScope = 'all' | OrderAccount

export interface OrderItem {
  account_kind: string
  order_id: string | null
  ticker: string | null
  name: string | null
  side: string | null
  type: string | null
  quantity: number | null
  filled_quantity: number | null
  limit_price: number | null
  stop_price: number | null
  fill_price: number | null
  status: string | null
  value: number | null
  created_at: string | null
  raw: Record<string, unknown>
}

export interface AccountError {
  account_kind: string
  code: string
  message: string
}

export interface OrdersResponse {
  orders: OrderItem[]
  errors: AccountError[]
}

export interface CancelOrderResponse {
  ok: boolean
  order_id: string
  account_kind: string
  message: string
}

export type ExecKeyTestStatus =
  | 'ok'
  | 'ip_restricted'
  | 'auth_failed'
  | 'error'
  | 'not_configured'
  | 'untested'

export interface ExecKeyTestResult {
  result: ExecKeyTestStatus
  code: string | null
  message: string | null
  checked_at: string | null
}

export interface AccountExecutionHealth {
  account_kind: string
  read_configured: boolean
  exec_configured: boolean
  exec_enabled: boolean
  last_test: ExecKeyTestResult
}

export interface ExecutionHealthResponse {
  broker_mode: string
  base_env: string
  egress_ip: string | null
  accounts: Record<string, AccountExecutionHealth>
}

export interface ExecKeyTestResponse {
  account_kind: string
  egress_ip: string | null
  test: ExecKeyTestResult
}

export async function getPendingOrders(account: OrderScope = 'all'): Promise<OrdersResponse> {
  const { data } = await api.get<OrdersResponse>('/orders/pending', { params: { account } })
  return data
}

export async function getOrderHistory(
  account: OrderScope = 'all',
  ticker?: string,
  limit = 50,
): Promise<OrdersResponse> {
  const { data } = await api.get<OrdersResponse>('/orders/history', {
    params: { account, ticker: ticker || undefined, limit },
  })
  return data
}

export async function cancelOrder(orderId: string, account: OrderAccount): Promise<CancelOrderResponse> {
  const { data } = await api.delete<CancelOrderResponse>(`/orders/${encodeURIComponent(orderId)}`, {
    params: { account },
  })
  return data
}

export async function getExecutionHealth(): Promise<ExecutionHealthResponse> {
  const { data } = await api.get<ExecutionHealthResponse>('/orders/execution-health')
  return data
}

export async function testExecutionKey(account: OrderAccount): Promise<ExecKeyTestResponse> {
  const { data } = await api.post<ExecKeyTestResponse>('/orders/execution-test', { account_kind: account })
  return data
}
