import { useCallback, useEffect, useRef, useState } from 'react'

export type PanelSize = { width: number; height: number }

const MIN: PanelSize = { width: 360, height: 420 }
const DEFAULT: PanelSize = { width: 480, height: 660 }

// The panel is pinned to the bottom-right, so it grows up and to the left. That
// is why this cannot be CSS `resize`, which only ever drags the bottom-right
// corner - on this anchor that pushes the panel off screen instead of enlarging
// it. The handle sits on the top-left corner and the deltas are inverted.
function clampToViewport(size: PanelSize): PanelSize {
  // 48px keeps the launcher and the window edge clear at both axes.
  const maxWidth = Math.max(MIN.width, window.innerWidth - 48)
  const maxHeight = Math.max(MIN.height, window.innerHeight - 112)
  return {
    width: Math.min(Math.max(size.width, MIN.width), maxWidth),
    height: Math.min(Math.max(size.height, MIN.height), maxHeight),
  }
}

export function useResizablePanel(storageKey: string) {
  const [size, setSize] = useState<PanelSize>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null')
      if (saved && typeof saved.width === 'number' && typeof saved.height === 'number') {
        return clampToViewport(saved)
      }
    } catch {
      // A corrupt entry is not worth a broken panel.
    }
    return DEFAULT
  })
  const origin = useRef<{ x: number; y: number; size: PanelSize } | null>(null)

  // A window that shrank below the saved size would otherwise leave the panel
  // taller than the viewport with its composer off screen.
  useEffect(() => {
    const onResize = () => setSize((current) => clampToViewport(current))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const persist = useCallback((next: PanelSize) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // Private mode. The size still applies for this session.
    }
  }, [storageKey])

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLElement>) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    origin.current = { x: event.clientX, y: event.clientY, size }
  }, [size])

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const start = origin.current
    if (!start) return
    setSize(clampToViewport({
      width: start.size.width - (event.clientX - start.x),
      height: start.size.height - (event.clientY - start.y),
    }))
  }, [])

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (!origin.current) return
    origin.current = null
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    setSize((current) => { persist(current); return current })
  }, [persist])

  // Keyboard is not an afterthought here: a pointer-only resize is unusable for
  // anyone who cannot drag, and this is the control that decides how much of
  // the answer they can read at once.
  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLElement>) => {
    const step = event.shiftKey ? 64 : 16
    const moves: Record<string, PanelSize> = {
      ArrowLeft: { width: step, height: 0 },
      ArrowRight: { width: -step, height: 0 },
      ArrowUp: { width: 0, height: step },
      ArrowDown: { width: 0, height: -step },
    }
    const delta = moves[event.key]
    if (!delta) return
    event.preventDefault()
    setSize((current) => {
      const next = clampToViewport({ width: current.width + delta.width, height: current.height + delta.height })
      persist(next)
      return next
    })
  }, [persist])

  return { size, handleProps: { onPointerDown, onPointerMove, onPointerUp, onKeyDown } }
}
