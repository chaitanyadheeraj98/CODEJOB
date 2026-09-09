import { useEffect, useRef, useState } from 'react'

type Item = { top: number; bottom: number; heading?: boolean }
export type PageSlice = { start: number; end: number }

export function paginate(items: Item[], height: number, total: number): PageSlice[] {
  const pages: PageSlice[] = []
  let start = 0
  while (start < total) {
    let end = Math.min(start + height, total)
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const bottom = item.heading ? (items[i + 1]?.bottom ?? item.bottom) : item.bottom
      // Breaking early only helps if the block then fits on the page it is
      // pushed to. A two-column body is one block several pages tall, and
      // moving it down just produced an almost-empty first page and the same
      // overflow underneath it. Blocks that cannot fit anywhere simply span.
      if (bottom - item.top > height) continue
      if (item.top > start && item.top < end && bottom > end) {
        end = item.top
        break
      }
    }
    pages.push({ start, end })
    start = end
  }
  return pages.length ? pages : [{ start: 0, end: height }]
}

export default function usePagination(contentKey: string, pageHeight: number) {
  const measureRef = useRef<HTMLDivElement>(null)
  const [pages, setPages] = useState<PageSlice[]>([{ start: 0, end: pageHeight }])
  useEffect(() => {
    const element = measureRef.current
    if (!element) return
    let disposed = false
    const measure = () => {
      if (disposed) return
      const origin = element.getBoundingClientRect().top
      const items = Array.from(element.children).map((child) => {
        const rect = child.getBoundingClientRect()
        return { top: rect.top - origin, bottom: rect.bottom - origin, heading: child.hasAttribute('data-heading') }
      })
      setPages(paginate(items, pageHeight, element.scrollHeight))
    }
    void (document.fonts?.ready ?? Promise.resolve()).then(measure)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    return () => { disposed = true; observer?.disconnect() }
  }, [contentKey, pageHeight])
  return { measureRef, pages }
}
