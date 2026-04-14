import { useState, useEffect } from 'react'
import { Truck, Bot, RefreshCw, Bell, ClipboardList, LayoutDashboard, Loader2 } from 'lucide-react'
import type { Stats, AgentStatus } from '../types'

interface Props {
  stats: Stats | null
  agentStatus: AgentStatus
  agentRunError: string | null
  agentRunning: boolean
  onRunAgent: () => void
  lastRefresh: Date
  tab: string
  onTabChange: (tab: 'dashboard' | 'cs-queue' | 'shift-summary') => void
  pendingNotifCount: number
  openExcCount: number
}

export function TopBar({
  stats,
  agentStatus,
  agentRunError,
  agentRunning,
  onRunAgent,
  tab,
  onTabChange,
  pendingNotifCount,
}: Props) {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const tabCls = (t: string) =>
    `flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-md transition-colors ${
      tab === t
        ? 'bg-blue-600 text-white'
        : 'text-slate-600 hover:bg-slate-100'
    }`

  const agentOk = agentStatus.status === 'completed'
  const agentErr = agentStatus.status === 'error'

  return (
    <header className="bg-white border-b border-slate-200 px-4 py-3 flex items-center gap-4 flex-wrap">
      {/* Brand */}
      <div className="flex items-center gap-2 mr-2">
        <Truck className="text-blue-600" size={22} />
        <span className="font-bold text-slate-900 text-lg tracking-tight">DispatchIQ</span>
      </div>

      {/* Clock */}
      <div className="text-slate-700 font-mono text-sm bg-slate-100 px-3 py-1.5 rounded-md">
        {now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </div>

      {/* Stats chips */}
      {stats && (
        <div className="flex items-center gap-2 text-sm">
          <StatChip label="Orders" value={stats.total_orders} color="blue" />
          <StatChip
            label="Delivered"
            value={stats.status_breakdown['delivered'] ?? 0}
            color="green"
          />
          <StatChip
            label="Exceptions"
            value={stats.open_exceptions}
            color={stats.open_exceptions > 0 ? 'red' : 'slate'}
          />
          <StatChip
            label="Drivers out"
            value={stats.drivers.called_out}
            color={stats.drivers.called_out > 0 ? 'amber' : 'slate'}
          />
        </div>
      )}

      <div className="flex-1" />

      {/* Tabs */}
      <nav className="flex items-center gap-1 bg-slate-100 rounded-lg p-1">
        <button className={tabCls('dashboard')} onClick={() => onTabChange('dashboard')}>
          <LayoutDashboard size={15} />
          Dashboard
        </button>
        <button className={tabCls('cs-queue')} onClick={() => onTabChange('cs-queue')}>
          <Bell size={15} />
          CS Queue
          {pendingNotifCount > 0 && (
            <span className="bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 ml-0.5 leading-none">
              {pendingNotifCount}
            </span>
          )}
        </button>
        <button className={tabCls('shift-summary')} onClick={() => onTabChange('shift-summary')}>
          <ClipboardList size={15} />
          Shift Summary
        </button>
      </nav>

      {/* Agent status */}
      <div className="flex flex-col items-end gap-1 max-w-md">
        <div className="flex items-center gap-2">
          <div
            className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-md font-medium ${
              agentRunning
                ? 'bg-blue-50 text-blue-700'
                : agentErr
                ? 'bg-red-50 text-red-700'
                : agentOk
                ? 'bg-green-50 text-green-700'
                : 'bg-slate-100 text-slate-500'
            }`}
          >
            <Bot size={13} />
            {agentRunning ? 'Agent running…' : agentOk ? 'Agent ready' : agentErr ? 'Agent error' : 'Agent idle'}
          </div>

          <button
            onClick={onRunAgent}
            disabled={agentRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-medium rounded-md transition-colors"
          >
            {agentRunning ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {agentRunning ? 'Running…' : 'Run Agent'}
          </button>
        </div>
        {agentRunError && (
          <p className="text-xs text-red-600 text-right leading-snug" title={agentRunError}>
            {agentRunError}
          </p>
        )}
      </div>
    </header>
  )
}

function StatChip({ label, value, color }: { label: string; value: number; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-700',
    green: 'bg-green-50 text-green-700',
    red: 'bg-red-50 text-red-700',
    amber: 'bg-amber-50 text-amber-700',
    slate: 'bg-slate-100 text-slate-600',
  }
  return (
    <div className={`px-2.5 py-1 rounded-md font-medium ${colorMap[color] ?? colorMap.slate}`}>
      <span className="font-bold">{value}</span>
      <span className="ml-1 opacity-75">{label}</span>
    </div>
  )
}
