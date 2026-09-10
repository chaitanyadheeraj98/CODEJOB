import { useEffect, useRef, useState } from 'react'

const FORMATS = [
  { fmt: 'pdf', label: 'Export as PDF', hint: 'Shareable, fixed layout' },
  { fmt: 'md', label: 'Export as Markdown', hint: 'Plain text, searchable' },
  { fmt: 'docx', label: 'Export as Word', hint: 'Editable document' },
] as const

// The whole conversation as a document. The server builds it from the stored
// transcript, so what downloads is the thread as recorded rather than whatever
// happens to be scrolled into view.
export function ExportMenu({ apiBase, sessionId }: { apiBase: string; sessionId: number | null }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const root = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (sessionId == null) return null

  const download = async (fmt: string) => {
    setBusy(fmt)
    setError('')
    try {
      const response = await fetch(`${apiBase}/chat/sessions/${sessionId}/export?fmt=${fmt}`)
      if (!response.ok) {
        // 503 is LibreOffice being unavailable or every render slot busy. The
        // server's own sentence says which, and says to take the .docx instead.
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail || `Could not build the ${fmt.toUpperCase()}.`)
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = response.headers.get('content-disposition')?.match(/filename="?([^"]+)"?/)?.[1] ?? `chat.${fmt}`
      link.click()
      URL.revokeObjectURL(url)
      setOpen(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Export failed')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="exportMenu" ref={root}>
      <button
        type="button"
        className="exportMenuTrigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        Export
      </button>
      {open ? (
        <div className="exportMenuList" role="menu">
          {FORMATS.map((item) => (
            <button
              key={item.fmt}
              type="button"
              role="menuitem"
              disabled={Boolean(busy)}
              onClick={() => void download(item.fmt)}
            >
              <span>{busy === item.fmt ? 'Building…' : item.label}</span>
              <small>{item.hint}</small>
            </button>
          ))}
          {error ? <p className="exportMenuError" role="alert">{error}</p> : null}
        </div>
      ) : null}
    </div>
  )
}
