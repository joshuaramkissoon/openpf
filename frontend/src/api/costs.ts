import axios from 'axios'
import type { CostSummary, UsageRecord } from '../types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export async function getCostSummary(): Promise<CostSummary> {
  const { data } = await api.get<CostSummary>('/costs/summary')
  return data
}

export async function getCostRecords(limit = 100): Promise<UsageRecord[]> {
  const { data } = await api.get<UsageRecord[]>('/costs/records', { params: { limit } })
  return data
}
