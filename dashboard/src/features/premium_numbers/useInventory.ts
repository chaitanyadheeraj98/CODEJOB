import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FilterValues } from '../../components/FilterSortBar'
import { inventoryDefaultFilterValues, inventoryFiltersToParams } from './inventoryFilters'

import {
  runContactBulkAction,
  runReviewAction,
  runReviewBulkAction,
  updateEmployerNumber,
  updateRecruiterNumber,
} from './api'
import type {
  InventoryAction,
  InventoryRow,
  ReviewEdits,
} from './types'

const PAGE_SIZE = 10

export function useInventory(apiBase: string, refreshToken = 0, externalFilterValues?: FilterValues, externalSort?: string, enabled = true) {
  const [rows, setRows] = useState<InventoryRow[]>([])
  const [filterValues, setFilterValues] = useState<FilterValues>(inventoryDefaultFilterValues)
  const [sort, setSort] = useState('newest')
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [busyBulkAction, setBusyBulkAction] = useState<InventoryAction | null>(null)
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)
  const effectiveFilterValues = externalFilterValues ?? filterValues
  const effectiveSort = externalSort ?? sort

  useEffect(() => { setPage(1) }, [effectiveFilterValues, effectiveSort])

  const load = useCallback(async () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ cursor:String((page-1)*PAGE_SIZE),limit:String(PAGE_SIZE),sort: effectiveSort })
      for(const [key,value] of Object.entries(inventoryFiltersToParams(effectiveFilterValues))) params.set(key,value)
      const response=await fetch(`${apiBase}/premium-numbers/inventory?${params}`)
      if(!response.ok) throw new Error('Failed to load premium number inventory')
      const payload=await response.json() as {items:InventoryRow[];total:number}
      if (requestId !== requestIdRef.current) return
      setRows(payload.items);setTotal(payload.total)
      setSelected((current) => new Set([...current].filter((key) => payload.items.some((row) => row.key === key))))
    } catch (reason) {
      if (requestId === requestIdRef.current) setError((reason as Error).message)
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [apiBase, effectiveFilterValues, effectiveSort, page])

  // Only the Number Inventory tab reads this list, and the sibling tabs supply
  // their own filter and sort vocabularies - Company Inventory's "most_contacts"
  // is a 422 here. Fetching while another tab is showing was always wasted work;
  // now it would also raise an error toast over a perfectly healthy page.
  useEffect(() => {
    if (!enabled) return
    const timer = window.setTimeout(() => { load().catch(() => undefined) }, 150)
    return () => window.clearTimeout(timer)
  }, [enabled, load, refreshToken])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const visibleRows = useMemo(
    () => rows,
    [rows],
  )

  const toggle = (key: string) => setSelected((current) => {
    const next = new Set(current)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  const selectVisible = (checked: boolean) => setSelected((current) => {
    const next = new Set(current)
    visibleRows.forEach((row) => checked ? next.add(row.key) : next.delete(row.key))
    return next
  })

  const dispatch = useCallback(async (targets: InventoryRow[], action: InventoryAction, edits?: ReviewEdits) => {
    if (targets.length === 0) return
    setBusy(true)
    setError('')
    try {
      const reviews = targets.filter((row) => row.kind === 'review')
      const recruiterContacts = targets.filter((row) => row.kind === 'contact' && row.recruiter)
      const employerContacts = targets.filter((row) => row.kind === 'contact' && !row.recruiter && row.employer)
      const tasks: Promise<void>[] = []
      if (reviews.length) {
        if (edits && reviews.length === 1 && (action === 'mark-recruiter' || action === 'mark-employer')) {
          tasks.push(runReviewAction(apiBase, reviews[0].id, action, edits))
        } else {
          tasks.push(runReviewBulkAction(apiBase, action, reviews.map((row) => row.id)))
        }
      }
      tasks.push(runContactBulkAction(apiBase, 'recruiter', action, recruiterContacts.map((row) => row.id)))
      tasks.push(runContactBulkAction(apiBase, 'employer', action, employerContacts.map((row) => row.id)))
      await Promise.all(tasks)
      setSelected(new Set())
      await load()
    } catch (reason) {
      setError((reason as Error).message)
      throw reason
    } finally {
      setBusy(false)
    }
  }, [apiBase, load])

  const runBulkAction = (action: InventoryAction) => {
    setBusyBulkAction(action)
    return dispatch(rows.filter((row) => selected.has(row.key)), action).finally(() => setBusyBulkAction(null))
  }
  const runRowAction = (row: InventoryRow, action: InventoryAction, edits?: ReviewEdits) => dispatch([row], action, edits)

  const toggleFavorite = useCallback(async (row: InventoryRow) => {
    if (row.kind !== 'contact') return
    const nextFavorite = !(row.recruiter?.is_favorite ?? row.employer?.is_favorite ?? false)
    setRows((current) => current.map((candidate) => candidate.key === row.key
      ? {
          ...candidate,
          recruiter: candidate.recruiter ? { ...candidate.recruiter, is_favorite: nextFavorite } : candidate.recruiter,
          employer: candidate.employer ? { ...candidate.employer, is_favorite: nextFavorite } : candidate.employer,
        }
      : candidate))
    try {
      if (row.recruiter) await updateRecruiterNumber(apiBase, row.id, { is_favorite: nextFavorite })
      else if (row.employer) await updateEmployerNumber(apiBase, row.id, { is_favorite: nextFavorite })
    } catch (reason) {
      setError((reason as Error).message)
      await load()
    }
  }, [apiBase, load])
  const updateFilter = (key:string,value:FilterValues[string]) => { setFilterValues((current)=>({...current,[key]:value}));setPage(1) }

  return {
    rows,
    total,
    visibleRows,
    filterValues,setFilterValues,updateFilter,sort,setSort:(value:string)=>{setSort(value);setPage(1)},
    page: safePage,
    setPage,
    totalPages,
    pageSize: PAGE_SIZE,
    selected,
    toggle,
    selectVisible,
    loading,
    busy,
    busyBulkAction,
    error,
    setError,
    reload: load,
    runBulkAction,
    runRowAction,
    toggleFavorite,
  }
}
