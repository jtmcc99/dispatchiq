import type {
  Order,
  Driver,
  Exception,
  CSNotification,
  Stats,
  AgentStatus,
  ShiftSummaryStructured,
} from '../types'

const configuredApiUrl = import.meta.env.VITE_API_URL?.trim()
const BASE = (configuredApiUrl && configuredApiUrl.length > 0
  ? configuredApiUrl
  : '/api'
).replace(/\/+$/, '')

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export const api = {
  orders: {
    list: (window?: string, zone?: string) => {
      const params = new URLSearchParams()
      if (window) params.set('window', window)
      if (zone) params.set('zone', zone)
      const qs = params.toString()
      return request<Order[]>(`/orders${qs ? `?${qs}` : ''}`)
    },
    get: (id: string) => request<Order>(`/orders/${id}`),
    updateStatus: (id: string, status: string, extra?: Record<string, unknown>) =>
      request<Order>(`/orders/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status, ...extra }),
      }),
    updatePickProgress: (id: string, items_picked: number) =>
      request<Order>(`/orders/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'picking', items_picked }),
      }),
  },

  drivers: {
    list: () => request<Driver[]>('/drivers'),
    updateStatus: (id: string, status: string) =>
      request<Driver>(`/drivers/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      }),
  },

  exceptions: {
    list: (status?: string) => {
      const qs = status ? `?status=${status}` : ''
      return request<Exception[]>(`/exceptions${qs}`)
    },
    resolve: (id: string) =>
      request<Exception>(`/exceptions/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'resolved' }),
      }),
  },

  csNotifications: {
    list: () => request<CSNotification[]>('/cs-notifications'),
    markHandled: (id: string) =>
      request<CSNotification>(`/cs-notifications/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'handled' }),
      }),
  },

  stats: {
    get: () => request<Stats>('/stats'),
  },

  agent: {
    run: () => request<AgentStatus>('/agent/run', { method: 'POST' }),
    status: () => request<AgentStatus>('/agent/status'),
    shiftSummary: () =>
      request<{ structured: ShiftSummaryStructured; generated_at: string }>('/agent/shift-summary'),
  },
}
