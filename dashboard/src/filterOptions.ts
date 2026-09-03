import { useEffect, useRef, useState } from 'react'

export async function fetchFilterOptions(
  apiBase: string,
  bucket: string,
  field: string,
  q: string,
  signal?: AbortSignal,
): Promise<string[]> {
  const params = new URLSearchParams({ bucket, field, limit: '20' })
  if (q.trim()) params.set('q', q.trim())
  const response = await fetch(`${apiBase}/filter-options?${params}`, { signal })
  if (!response.ok) throw new Error('Failed to load filter options')
  return ((await response.json()) as { values?: string[] }).values ?? []
}

export type FilterOptionsState = { values: string[]; loading: boolean; error: boolean }

const EMPTY_STATE: FilterOptionsState = { values: [], loading: false, error: false }

export function useFilterOptions(
  apiBase: string,
  bucket: string,
  field: string,
  query: string,
  enabled: boolean,
): FilterOptionsState {
  const [state, setState] = useState<FilterOptionsState>(EMPTY_STATE)
  const wasEnabled = useRef(false)

  useEffect(() => {
    const opening = enabled && !wasEnabled.current
    wasEnabled.current = enabled
    if (!enabled) {
      setState(EMPTY_STATE)
      return
    }

    const controller = new AbortController()
    const timer = setTimeout(() => {
      setState((current) => ({ ...current, loading: true, error: false }))
      fetchFilterOptions(apiBase, bucket, field, query, controller.signal)
        .then((values) => setState({ values, loading: false, error: false }))
        .catch((reason: unknown) => {
          if ((reason as { name?: string }).name !== 'AbortError') {
            setState({ values: [], loading: false, error: true })
          }
        })
    }, opening && !query.trim() ? 0 : 300)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [apiBase, bucket, enabled, field, query])

  return state
}
