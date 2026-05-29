import { useState, useEffect, useCallback } from 'react'
import { api } from './api/client'
import type { Order, Driver, Exception, CSNotification, Stats, AgentStatus } from './types'
import { TopBar } from './components/TopBar'
import { DriverPanel } from './components/DriverPanel'
import { OrdersView } from './components/OrdersView'
import { ExceptionFeed } from './components/ExceptionFeed'
import { CSQueue } from './components/CSQueue'
import { ShiftSummary } from './components/ShiftSummary'

type Tab = 'dashboard' | 'cs-queue' | 'shift-summary'

function tabFromSearch(): Tab {
  const q = new URLSearchParams(window.location.search).get('tab')
  if (q === 'cs-queue' || q === 'shift-summary' || q === 'dashboard') return q
  return 'dashboard'
}

export default function App() {
  const [tab, setTab] = useState<Tab>(tabFromSearch)
  const [selectedWindow, setSelectedWindow] = useState<string | null>(null)

  const [orders, setOrders] = useState<Order[]>([])
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [exceptions, setExceptions] = useState<Exception[]>([])
  const [notifications, setNotifications] = useState<CSNotification[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [agentStatus, setAgentStatus] = useState<AgentStatus>({})
  const [agentRunError, setAgentRunError] = useState<string | null>(null)
  const [agentRunning, setAgentRunning] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

  const fetchAll = useCallback(async () => {
    try {
      const [o, d, e, n, s, a] = await Promise.all([
        api.orders.list(),
        api.drivers.list(),
        api.exceptions.list(),
        api.csNotifications.list(),
        api.stats.get(),
        api.agent.status(),
      ])
      setOrders(o)
      setDrivers(d)
      setExceptions(e)
      setNotifications(n)
      setStats(s)
      setAgentStatus(a)
      setLastRefresh(new Date())
    } catch (err) {
      console.error('Fetch error', err)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 8000)
    return () => clearInterval(interval)
  }, [fetchAll])

  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('tab', tab)
    window.history.replaceState({}, '', url)
  }, [tab])

  const runAgent = async () => {
    setAgentRunError(null)
    setAgentRunning(true)
    try {
      const result = await api.agent.run()
      setAgentStatus(result)
      await fetchAll()
    } catch (err) {
      console.error('Agent error', err)
      const raw = err instanceof Error ? err.message : String(err)
      if (raw.startsWith('502') || raw.includes('Failed to fetch')) {
        setAgentRunError(
          'API unreachable. In another terminal: cd backend && source ../venv/bin/activate && uvicorn main:app --reload --port 8000',
        )
      } else if (
        raw.includes('ANTHROPIC_API_KEY') ||
        raw.includes('authentication method') ||
        raw.includes('Run Agent unavailable')
      ) {
        setAgentRunError(
          'Run Agent is disabled on this deployment because the backend Anthropic API key is not configured.',
        )
      } else {
        setAgentRunError(raw.replace(/^\d+\s*:\s*/, '').slice(0, 200) || 'Run Agent failed')
      }
    } finally {
      setAgentRunning(false)
    }
  }

  const resolveException = async (id: string) => {
    await api.exceptions.resolve(id)
    setExceptions(prev => prev.map(e => e.id === id ? { ...e, status: 'resolved' as const } : e))
  }

  const markNotificationHandled = async (id: string) => {
    await api.csNotifications.markHandled(id)
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, status: 'handled' as const } : n))
  }

  const updateOrderStatus = async (orderId: string, status: string) => {
    await api.orders.updateStatus(orderId, status)
    await fetchAll()
  }

  const pendingNotifCount = notifications.filter(n => n.status === 'pending').length
  const openExcCount = exceptions.filter(e => e.status === 'open').length

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col" style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <TopBar
        stats={stats}
        agentStatus={agentStatus}
        agentRunError={agentRunError}
        agentRunning={agentRunning}
        onRunAgent={runAgent}
        lastRefresh={lastRefresh}
        tab={tab}
        onTabChange={setTab}
        pendingNotifCount={pendingNotifCount}
        openExcCount={openExcCount}
      />

      <div className="flex flex-1 overflow-hidden">
        {tab === 'dashboard' && (
          <>
            <DriverPanel drivers={drivers} stats={stats} />

            <div className="flex flex-1 overflow-hidden">
              <OrdersView
                orders={orders}
                stats={stats}
                selectedWindow={selectedWindow}
                onSelectWindow={setSelectedWindow}
                onUpdateStatus={updateOrderStatus}
              />

              <ExceptionFeed
                exceptions={exceptions}
                onResolve={resolveException}
                agentRunning={agentRunning}
                agentStatus={agentStatus}
              />
            </div>
          </>
        )}

        {tab === 'cs-queue' && (
          <div className="flex-1 p-6 overflow-auto">
            <CSQueue
              notifications={notifications}
              onMarkHandled={markNotificationHandled}
            />
          </div>
        )}

        {tab === 'shift-summary' && (
          <div className="flex-1 p-6 overflow-auto">
            <ShiftSummary stats={stats} exceptions={exceptions} />
          </div>
        )}
      </div>
    </div>
  )
}
