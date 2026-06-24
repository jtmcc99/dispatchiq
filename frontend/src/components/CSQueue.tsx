import { CheckCircle, Bell, Clock, User, Zap, Package } from 'lucide-react'
import type { CSNotification } from '../types'
import { timeAgo } from '../utils/time'

interface Props {
  notifications: CSNotification[]
  onMarkHandled: (id: string) => void
}

const ISSUE_COLOR: Record<string, string> = {
  missing_core_item: 'bg-red-100 text-red-700 border-red-200',
  missing_items_batch: 'bg-amber-100 text-amber-700 border-amber-200',
  oos_minor: 'bg-amber-100 text-amber-700 border-amber-200',
  late_delivery: 'bg-orange-100 text-orange-700 border-orange-200',
  coverage_gap: 'bg-purple-100 text-purple-700 border-purple-200',
  delivery_dispute: 'bg-blue-100 text-blue-700 border-blue-200',
}

function ImmediateBadge() {
  return (
    <span className="flex items-center gap-1 text-xs bg-red-600 text-white px-2 py-0.5 rounded font-bold">
      <Zap size={10} />
      IMMEDIATE — Core Item
    </span>
  )
}

function BatchedBadge() {
  return (
    <span className="flex items-center gap-1 text-xs bg-amber-500 text-white px-2 py-0.5 rounded font-semibold">
      <Package size={10} />
      BATCHED — Order Complete
    </span>
  )
}

export function CSQueue({ notifications, onMarkHandled }: Props) {
  // pending_batch is hidden (not ready for CS yet — will be batched when picking completes)
  const visible = notifications.filter(n => n.status !== 'pending_batch')
  const pending = visible.filter(n => n.status === 'pending')
  const handled = visible.filter(n => n.status === 'handled')

  // Sort: immediate first, then batched, then standard
  const sortedPending = [...pending].sort((a, b) => {
    const order = { immediate: 0, standard: 1, batched: 2 }
    return (order[a.notification_subtype] ?? 1) - (order[b.notification_subtype] ?? 1)
  })

  return (
    <div className="max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Bell size={20} className="text-blue-600" />
        <div>
          <h1 className="text-xl font-bold text-slate-900">CS Notification Queue</h1>
          <p className="text-sm text-slate-500">
            Agent-generated alerts that need customer communication
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-sm flex-wrap justify-end">
          <span className="bg-red-100 text-red-700 px-3 py-1 rounded-full font-semibold">
            {pending.filter(n => n.notification_subtype === 'immediate').length} immediate
          </span>
          <span className="bg-amber-100 text-amber-700 px-3 py-1 rounded-full font-semibold">
            {pending.filter(n => n.notification_subtype !== 'immediate').length} batched
          </span>
          <span className="bg-slate-100 text-slate-600 px-3 py-1 rounded-full">
            {handled.length} handled
          </span>
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mb-4 text-xs text-slate-500">
        <div className="flex items-center gap-1.5">
          <Zap size={11} className="text-red-600" />
          <span><strong className="text-red-700">IMMEDIATE</strong> — sent before dispatch, customer must know now</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Package size={11} className="text-amber-600" />
          <span><strong className="text-amber-700">BATCHED</strong> — consolidated when picking finished</span>
        </div>
      </div>

      {/* Pending notifications */}
      {sortedPending.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400 bg-white rounded-xl border border-slate-200">
          <CheckCircle size={40} className="mb-3 opacity-30" />
          <p className="font-medium">All caught up!</p>
          <p className="text-sm mt-1">No pending CS notifications</p>
        </div>
      ) : (
        <div className="space-y-3 mb-8">
          {sortedPending.map(n => (
            <NotificationCard key={n.id} notif={n} onMarkHandled={onMarkHandled} />
          ))}
        </div>
      )}

      {/* Handled */}
      {handled.length > 0 && (
        <>
          <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
            Handled ({handled.length})
          </h2>
          <div className="space-y-2 opacity-60">
            {handled.slice(0, 10).map(n => (
              <NotificationCard key={n.id} notif={n} onMarkHandled={onMarkHandled} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function NotificationCard({
  notif,
  onMarkHandled,
}: {
  notif: CSNotification
  onMarkHandled: (id: string) => void
}) {
  const isHandled = notif.status === 'handled'
  const isImmediate = notif.notification_subtype === 'immediate'
  const isBatched = notif.notification_subtype === 'batched'

  const borderClass = isImmediate
    ? 'border-red-300 shadow-sm shadow-red-100'
    : isBatched
    ? 'border-amber-200'
    : 'border-slate-200'

  return (
    <div className={`bg-white rounded-xl border ${isHandled ? 'border-slate-100' : borderClass} p-4`}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          {/* Top row: badges + meta */}
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            {isImmediate && <ImmediateBadge />}
            {isBatched && <BatchedBadge />}
            {!isImmediate && !isBatched && (
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full border capitalize ${
                ISSUE_COLOR[notif.issue_type] ?? 'bg-slate-100 text-slate-600 border-slate-200'
              }`}>
                {notif.issue_type.replace(/_/g, ' ')}
              </span>
            )}
            {notif.order_id && (
              <span className="font-mono text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                {notif.order_id}
              </span>
            )}
            <span className="text-xs text-slate-400 flex items-center gap-1 ml-auto">
              <Clock size={11} />
              {timeAgo(notif.created_at)}
            </span>
          </div>

          {/* Customer */}
          {notif.customer_name && (
            <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800 mb-1">
              <User size={13} className="text-slate-400" />
              {notif.customer_name}
            </div>
          )}

          {/* Internal detail */}
          <p className="text-xs text-slate-500 mb-3">{notif.details}</p>

          {/* Customer message box */}
          <div className={`rounded-lg p-3 border ${
            isImmediate
              ? 'bg-red-50 border-red-100'
              : isBatched
              ? 'bg-amber-50 border-amber-100'
              : 'bg-blue-50 border-blue-100'
          }`}>
            <div className={`text-xs font-semibold mb-1 flex items-center gap-1 ${
              isImmediate ? 'text-red-600' : isBatched ? 'text-amber-700' : 'text-blue-600'
            }`}>
              What to tell the customer:
            </div>
            <p className={`text-sm leading-relaxed ${
              isImmediate ? 'text-red-900' : isBatched ? 'text-amber-900' : 'text-blue-900'
            }`}>
              {notif.customer_message}
            </p>
          </div>
        </div>

        {/* Action */}
        <div className="flex-shrink-0">
          {isHandled ? (
            <div className="flex items-center gap-1 text-green-600 text-xs font-medium">
              <CheckCircle size={15} />
              Handled
            </div>
          ) : (
            <button
              onClick={() => onMarkHandled(notif.id)}
              className="flex items-center gap-1.5 px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded-lg transition-colors"
            >
              <CheckCircle size={14} />
              Mark Handled
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
