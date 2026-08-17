export function formatRelativeInboxTime(value: string, now = new Date()): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value

  const dayNumber = (date: Date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
  const daysAgo = (dayNumber(now) - dayNumber(parsed)) / 86_400_000
  if (daysAgo === 0) {
    return parsed.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  }
  if (daysAgo === 1) return 'Yesterday'
  return parsed.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(parsed.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  })
}

export function getInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  return words.slice(0, 2).map((word) => word[0]).join('').toUpperCase() || '?'
}
