import { useEffect, useMemo, useRef, useState } from 'react'
import { addSavedQuery, findExactSavedQuery, removeSavedQuery } from './state'

type QueryBucketProps = {
  queryValue: string
  savedQueries: string[]
  onQueryChange: (value: string) => void
  onQuerySelect: (value: string) => void
  onSavedQueriesChange: (values: string[]) => Promise<void>
}

export default function QueryBucket({
  queryValue,
  savedQueries,
  onQueryChange,
  onQuerySelect,
  onSavedQueriesChange,
}: QueryBucketProps) {
  const [message, setMessage] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState<number>(-1)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const matchedSavedQuery = useMemo(() => {
    const current = queryValue.trim().toLowerCase()
    const match = savedQueries.find((item) => item.trim().toLowerCase() === current)
    return match ?? null
  }, [queryValue, savedQueries])
  const filteredSuggestions = useMemo(() => {
    const needle = queryValue.trim().toLowerCase()
    if (!needle) return savedQueries
    return savedQueries.filter((item) => item.toLowerCase().includes(needle))
  }, [queryValue, savedQueries])

  useEffect(() => {
    const onDocMouseDown = (event: MouseEvent) => {
      const target = event.target as Node | null
      if (wrapRef.current && target && !wrapRef.current.contains(target)) {
        setIsOpen(false)
        setHighlightedIndex(-1)
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [])

  useEffect(() => {
    if (highlightedIndex >= filteredSuggestions.length) {
      setHighlightedIndex(filteredSuggestions.length > 0 ? 0 : -1)
    }
  }, [filteredSuggestions, highlightedIndex])

  const saveCurrentQuery = async () => {
    const result = addSavedQuery(savedQueries, queryValue)
    if (!result.ok) {
      if (result.reason === 'empty') setMessage('Type a query first.')
      if (result.reason === 'duplicate') setMessage('Query already saved.')
      if (result.reason === 'limit') setMessage('Saved query limit reached (10).')
      return
    }
    await onSavedQueriesChange(result.next)
    setMessage('Saved.')
  }

  const deleteCurrentQuery = async () => {
    const matched = findExactSavedQuery(savedQueries, queryValue)
    if (!matched) {
      setMessage('Current query is not in saved list.')
      return
    }
    await onSavedQueriesChange(removeSavedQuery(savedQueries, matched))
    setMessage('Removed.')
  }

  const selectSuggestion = (value: string) => {
    onQueryChange(value)
    onQuerySelect(value)
    setIsOpen(false)
    setHighlightedIndex(-1)
  }

  return (
    <div className="queryBucketInline" ref={wrapRef}>
      <input
        className="queryBucketInput"
        value={queryValue}
        onChange={(e) => {
          onQueryChange(e.target.value)
          if (!isOpen && savedQueries.length > 0) setIsOpen(true)
        }}
        onFocus={() => {
          if (savedQueries.length > 0) {
            setIsOpen(true)
            setHighlightedIndex(filteredSuggestions.length > 0 ? 0 : -1)
          }
        }}
        onBlur={(e) => {
          if (!e.target.value) return
          onQuerySelect(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setIsOpen(false)
            setHighlightedIndex(-1)
            return
          }
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            if (!isOpen && filteredSuggestions.length > 0) {
              setIsOpen(true)
              setHighlightedIndex(0)
              return
            }
            if (filteredSuggestions.length > 0) {
              setHighlightedIndex((prev) => (prev + 1) % filteredSuggestions.length)
            }
            return
          }
          if (e.key === 'ArrowUp') {
            e.preventDefault()
            if (!isOpen && filteredSuggestions.length > 0) {
              setIsOpen(true)
              setHighlightedIndex(filteredSuggestions.length - 1)
              return
            }
            if (filteredSuggestions.length > 0) {
              setHighlightedIndex((prev) => (prev <= 0 ? filteredSuggestions.length - 1 : prev - 1))
            }
            return
          }
          if (e.key === 'Enter') {
            e.preventDefault()
            if (isOpen && highlightedIndex >= 0 && highlightedIndex < filteredSuggestions.length) {
              selectSuggestion(filteredSuggestions[highlightedIndex])
              return
            }
            onQuerySelect(queryValue)
          }
        }}
        placeholder="tx is:unread"
        aria-label="Gmail query"
      />
      {isOpen && filteredSuggestions.length > 0 ? (
        <div className="querySuggestionList" role="listbox" aria-label="Saved query suggestions">
          {filteredSuggestions.map((item, index) => (
            <button
              key={item}
              type="button"
              className={`querySuggestionItem${index === highlightedIndex ? ' active' : ''}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => selectSuggestion(item)}
              role="option"
              aria-selected={index === highlightedIndex}
            >
              {item}
            </button>
          ))}
        </div>
      ) : null}
      <button type="button" className="queryOverlayBtn queryOverlayAdd" onClick={() => { void saveCurrentQuery() }} title="Save current query">
        +
      </button>
      <button
        type="button"
        className="queryOverlayBtn queryOverlayRemove"
        onClick={() => { void deleteCurrentQuery() }}
        title={matchedSavedQuery ? `Remove saved query: ${matchedSavedQuery}` : 'Remove current saved query'}
      >
        -
      </button>
      {message ? <p className="queryBucketMsgInline">{message}</p> : null}
    </div>
  )
}
