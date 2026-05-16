import { QUERY_BUCKET_LIMIT, type QueryBucketResult } from './types'

export function normalizeQuery(value: string): string {
  return value.trim()
}

export function addSavedQuery(current: string[], rawValue: string): QueryBucketResult {
  const normalized = normalizeQuery(rawValue)
  if (!normalized) return { ok: false, reason: 'empty', next: current }
  const exists = current.some((item) => item.trim().toLowerCase() === normalized.toLowerCase())
  if (exists) return { ok: false, reason: 'duplicate', next: current }
  if (current.length >= QUERY_BUCKET_LIMIT) return { ok: false, reason: 'limit', next: current }
  return { ok: true, next: [...current, normalized] }
}

export function removeSavedQuery(current: string[], valueToRemove: string): string[] {
  const target = valueToRemove.trim().toLowerCase()
  return current.filter((item) => item.trim().toLowerCase() !== target)
}

export function findExactSavedQuery(current: string[], rawValue: string): string | null {
  const target = normalizeQuery(rawValue).toLowerCase()
  if (!target) return null
  const match = current.find((item) => item.trim().toLowerCase() === target)
  return match ?? null
}
