/**
 * Format an ISO timestamp as a relative "X ago" string with compound units.
 *
 *   29s   → "29s ago"
 *   2m14s → "2m 14s ago"
 *   1h05m → "1h 5m ago"
 *   3d 2h → "3d 2h ago"
 *
 * Handles both timezone-aware ISO strings (`2026-06-24T20:03:30+00:00`) and
 * legacy naive strings (`2026-06-24T20:03:30`) by treating the naive form as
 * UTC. The backend now writes timezone-aware timestamps, but records already
 * on disk from earlier runs are naive — without this normalization the
 * dashboard would show negative-seconds values for legacy data.
 *
 * Negative diffs (clock skew, or a timestamp the future for any reason) are
 * clamped to zero rather than rendered as "-3h ago", which is never the right
 * thing to surface to a user.
 */
export function timeAgo(iso: string): string {
  const hasTz = /[zZ]|[+-]\d{2}:\d{2}$/.test(iso)
  const normalized = hasTz ? iso : `${iso}Z`
  const parsed = new Date(normalized).getTime()
  if (Number.isNaN(parsed)) return ''

  let diff = Math.floor((Date.now() - parsed) / 1000)
  if (diff < 0) diff = 0
  if (diff < 1) return 'just now'

  const days = Math.floor(diff / 86400)
  const hours = Math.floor((diff % 86400) / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  const seconds = diff % 60

  if (days > 0) return `${days}d ${hours}h ago`
  if (hours > 0) return `${hours}h ${minutes}m ago`
  if (minutes > 0) return `${minutes}m ${seconds}s ago`
  return `${seconds}s ago`
}
