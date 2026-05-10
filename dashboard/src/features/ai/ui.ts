import type { DraftSource } from './types'

export const getDraftSourceLabel = (source?: string | null): string => {
  const value = (source ?? '').toLowerCase() as DraftSource
  if (value === 'deepseek') return 'DeepSeek'
  if (value === 'rules_only') return 'Rules fallback'
  return source || 'Unknown'
}

