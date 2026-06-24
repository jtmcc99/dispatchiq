import { CheckCircle, Bot, Clock, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import type { Exception, AgentStatus } from '../types'
import { timeAgo } from '../utils/time'

interface Props {
  exceptions: Exception[]
  onResolve: (id: string) => void
  agentRunning: boolean
  agentStatus: AgentStatus
}

const TYPE_LABEL: Record<string, string> = {
  late_risk: 'Late Risk',
  missing_item: 'Missing Item',
  coverage_gap: 'Coverage Gap',
  delivery_dispute: 'Dispute',
}

const TYPE_COLOR: Record<string, string> = {
  late_risk: 'text-amber-700 bg-amber-50 border-amber-200',
  missing_item: 'text-red-700 bg-red-50 border-red-200',
  coverage_gap: 'text-orange-700 bg-orange-50 border-orange-200',
  delivery_dispute: 'text-purple-700 bg-purple-50 border-purple-200',
}

const SEV_DOT: Record<string, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-slate-400',
}

export function ExceptionFeed({ exceptions, onResolve, agentRunning, agentStatus }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showResolved, setShowResolved] = useState(false)

  const open = exceptions.filter(e => e.status !== 'resolved')
  const resolved = exceptions.filter(e => e.status === 'resolved')
  const shown = showResolved ? exceptions : open

  return (
    <aside className="w-80 bg-white border-l border-slate-200 flex flex-col overflow-hidden">
      <div className="px-4 pt-4 pb-3 border-b border-slate-100 flex-shrink-0">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
            <Bot size={15} className="text-blue-500" />
            Agent Exceptions
            {open.length > 0 && (
              <span className="bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 font-bold leading-none">
                {open.length}
              </span>
            )}
          </h2>
          <button
            onClick={() => setShowResolved(!showResolved)}
            className="text-xs text-slate-500 hover:text-slate-700"
          >
            {showResolved ? 'Hide resolved' : `+${resolved.length} resolved`}
          </button>
        </div>

        {/* Agent last run summary */}
        {agentStatus.summary && (
          <div className="mt-2 text-xs text-slate-500 bg-slate-50 rounded p-2 line-clamp-2">
            {agentStatus.timestamp && (
              <span className="text-blue-500 font-medium mr-1">
                {timeAgo(agentStatus.timestamp)}:
              </span>
            )}
            {agentStatus.summary}
          </div>
        )}

        {agentRunning && (
          <div className="mt-2 flex items-center gap-2 text-xs text-blue-600 bg-blue-50 rounded p-2">
            <Bot size={12} className="animate-pulse" />
            Agent is analyzing operations…
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto p-2 space-y-2">
        {shown.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-slate-400">
            <CheckCircle size={28} className="mb-2 opacity-40" />
            <p className="text-sm">No open exceptions</p>
            <p className="text-xs mt-1">Run the agent to check</p>
          </div>
        ) : (
          shown.map(exc => (
            <ExceptionCard
              key={exc.id}
              exc={exc}
              expanded={expandedId === exc.id}
              onToggle={() => setExpandedId(expandedId === exc.id ? null : exc.id)}
              onResolve={onResolve}
            />
          ))
        )}
      </div>
    </aside>
  )
}

function ExceptionCard({
  exc,
  expanded,
  onToggle,
  onResolve,
}: {
  exc: Exception
  expanded: boolean
  onToggle: () => void
  onResolve: (id: string) => void
}) {
  const isResolved = exc.status === 'resolved'

  return (
    <div
      className={`rounded-lg border text-sm transition-all ${
        isResolved
          ? 'border-slate-100 bg-slate-50 opacity-60'
          : TYPE_COLOR[exc.type] ?? 'border-slate-200 bg-white'
      }`}
    >
      <div
        className="flex items-start gap-2 p-2.5 cursor-pointer"
        onClick={onToggle}
      >
        <div className={`w-2 h-2 rounded-full flex-shrink-0 mt-1 ${SEV_DOT[exc.severity]}`} />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="font-semibold text-xs uppercase tracking-wide">
              {TYPE_LABEL[exc.type]}
            </span>
            {exc.order_id && (
              <span className="font-mono text-xs opacity-70">{exc.order_id}</span>
            )}
          </div>
          <p className="text-xs leading-snug line-clamp-2">{exc.description}</p>
          <div className="flex items-center gap-1.5 mt-1 text-xs opacity-60">
            <Clock size={10} />
            {timeAgo(exc.created_at)}
          </div>
        </div>

        <div className="flex-shrink-0">
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </div>
      </div>

      {expanded && (
        <div className="px-3 pb-3 space-y-2">
          <div className="bg-white rounded border border-current border-opacity-20 p-2 text-xs">
            <div className="font-semibold mb-1 flex items-center gap-1">
              <Bot size={11} /> Agent Recommendation
            </div>
            <p className="leading-relaxed">{exc.agent_recommendation}</p>
          </div>

          {!isResolved && (
            <button
              onClick={() => onResolve(exc.id)}
              className="w-full flex items-center justify-center gap-1.5 py-1.5 bg-green-600 hover:bg-green-700 text-white text-xs font-medium rounded-md transition-colors"
            >
              <CheckCircle size={12} />
              Mark Resolved
            </button>
          )}
        </div>
      )}
    </div>
  )
}
