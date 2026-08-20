import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  listEmployerNumbers,
  listRecruiterNumbers,
  listReviewNumbers,
  runContactBulkAction,
  runReviewAction,
  runReviewBulkAction,
} from './api'
import type {
  EmployerNumberCard,
  InventoryAction,
  InventoryCategoryFilter,
  InventoryRow,
  InventorySourceFilter,
  InventoryStatusFilter,
  RecruiterNumberCard,
  ReviewEdits,
} from './types'

const PAGE_SIZE = 10

export function reviewRow(review: NonNullable<InventoryRow['review']>): InventoryRow {
  return {
    key: `review:${review.id}`,
    kind: 'review',
    id: review.id,
    number: review.display_phone_number,
    owner: review.owner_name,
    company: review.company,
    categories: [],
    status: 'Pending',
    score: review.recruiter_relevance_score || null,
    sourceType: review.source_external_opportunity_id ? 'nvoids' : 'gmail',
    lastCheckedAt: review.updated_at,
    review,
  }
}

export function mergeContacts(recruiters: RecruiterNumberCard[], employers: EmployerNumberCard[]): InventoryRow[] {
  const contacts = new Map<number, InventoryRow>()
  for (const recruiter of recruiters) {
    contacts.set(recruiter.id, {
      key: `contact:${recruiter.id}`,
      kind: 'contact',
      id: recruiter.id,
      number: recruiter.display_phone_number,
      owner: recruiter.recruiter_name,
      company: recruiter.company,
      categories: [
        ...(recruiter.is_recruiter ? ['Recruiter' as const] : []),
        ...(recruiter.is_employer ? ['Employer' as const] : []),
      ],
      status: recruiter.flagged ? 'Flagged' : 'Active',
      score: recruiter.recruiter_relevance_score,
      sourceType: recruiter.source_type,
      lastCheckedAt: recruiter.updated_at,
      recruiter,
    })
  }
  for (const employer of employers) {
    const existing = contacts.get(employer.id)
    if (existing) {
      existing.employer = employer
      existing.categories = Array.from(new Set([
        ...existing.categories,
        ...(employer.is_recruiter ? ['Recruiter' as const] : []),
        ...(employer.is_employer ? ['Employer' as const] : []),
      ]))
      if (employer.flagged) existing.status = 'Flagged'
      if (existing.score == null) existing.score = employer.recruiter_relevance_score
      if (!existing.sourceType) existing.sourceType = employer.source_type
      if (new Date(employer.updated_at).getTime() > new Date(existing.lastCheckedAt).getTime()) {
        existing.lastCheckedAt = employer.updated_at
      }
      continue
    }
    contacts.set(employer.id, {
      key: `contact:${employer.id}`,
      kind: 'contact',
      id: employer.id,
      number: employer.display_phone_number,
      owner: employer.owner_name,
      company: employer.company,
      categories: [
        ...(employer.is_recruiter ? ['Recruiter' as const] : []),
        ...(employer.is_employer ? ['Employer' as const] : []),
      ],
      status: employer.flagged ? 'Flagged' : 'Active',
      score: employer.recruiter_relevance_score,
      sourceType: employer.source_type,
      lastCheckedAt: employer.updated_at,
      employer,
    })
  }
  return [...contacts.values()]
}

export function useInventory(apiBase: string, refreshToken = 0) {
  const [rows, setRows] = useState<InventoryRow[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<InventoryStatusFilter>('all')
  const [category, setCategory] = useState<InventoryCategoryFilter>('all')
  const [source, setSource] = useState<InventorySourceFilter>('all')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)

  const load = useCallback(async () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setLoading(true)
    setError('')
    try {
      const includePending = status === 'all' || status === 'Pending'
      const flagModes = status === 'Flagged' ? [true] : status === 'Active' ? [false] : [false, true]
      const reviewPromise = includePending ? listReviewNumbers(apiBase, search) : Promise.resolve([])
      const contactPromises = status === 'Pending' ? [] : flagModes.flatMap((flagged) => [
        listRecruiterNumbers(apiBase, search, source, flagged),
        listEmployerNumbers(apiBase, search, source, flagged),
      ])
      const [reviews, ...contactResults] = await Promise.all([reviewPromise, ...contactPromises])
      if (requestId !== requestIdRef.current) return
      const recruiters: RecruiterNumberCard[] = []
      const employers: EmployerNumberCard[] = []
      contactResults.forEach((result, index) => {
        if (index % 2 === 0) recruiters.push(...result as RecruiterNumberCard[])
        else employers.push(...result as EmployerNumberCard[])
      })
      const normalized = [
        ...reviews.map((review) => reviewRow(review)),
        ...mergeContacts(recruiters, employers),
      ]
        .filter((row) => source === 'all' || row.sourceType === source)
        .filter((row) => category === 'all' || row.categories.includes(category))
        .filter((row) => status === 'all' || row.status === status)
        .sort((left, right) => new Date(right.lastCheckedAt).getTime() - new Date(left.lastCheckedAt).getTime())
      setRows(normalized)
      setSelected((current) => new Set([...current].filter((key) => normalized.some((row) => row.key === key))))
    } catch (reason) {
      if (requestId === requestIdRef.current) setError((reason as Error).message)
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [apiBase, category, search, source, status])

  useEffect(() => {
    const timer = window.setTimeout(() => { load().catch(() => undefined) }, 150)
    return () => window.clearTimeout(timer)
  }, [load, refreshToken])

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const visibleRows = useMemo(
    () => rows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [rows, safePage],
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

  const runBulkAction = (action: InventoryAction) => dispatch(rows.filter((row) => selected.has(row.key)), action)
  const runRowAction = (row: InventoryRow, action: InventoryAction, edits?: ReviewEdits) => dispatch([row], action, edits)
  const updateSearch = (value: string) => { setSearch(value); setPage(1) }
  const updateStatus = (value: InventoryStatusFilter) => { setStatus(value); setPage(1) }
  const updateCategory = (value: InventoryCategoryFilter) => { setCategory(value); setPage(1) }
  const updateSource = (value: InventorySourceFilter) => { setSource(value); setPage(1) }

  return {
    rows,
    visibleRows,
    search,
    setSearch: updateSearch,
    status,
    setStatus: updateStatus,
    category,
    setCategory: updateCategory,
    source,
    setSource: updateSource,
    page: safePage,
    setPage,
    totalPages,
    pageSize: PAGE_SIZE,
    selected,
    toggle,
    selectVisible,
    loading,
    busy,
    error,
    setError,
    reload: load,
    runBulkAction,
    runRowAction,
  }
}
