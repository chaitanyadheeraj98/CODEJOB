import { useCallback, useEffect, useState } from 'react'

const TOAST_DURATION_MS = 2500

export type ToastTone = 'success' | 'error'

export function useToast() {
  const [message, setMessage] = useState<string | null>(null)
  const [tone, setTone] = useState<ToastTone>('success')
  const show = useCallback((text: string, nextTone: ToastTone = 'success') => {
    setTone(nextTone)
    setMessage(text)
  }, [])
  const clear = useCallback(() => setMessage(null), [])
  return { message, tone, show, clear }
}

type ToastHostProps = {
  message: string | null
  tone?: ToastTone
  onDismiss: () => void
}

export function ToastHost({ message, tone = 'success', onDismiss }: ToastHostProps) {
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(onDismiss, TOAST_DURATION_MS)
    return () => window.clearTimeout(timer)
  }, [message, onDismiss])

  if (!message) return null
  return <div className={`actionToast actionToast--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{message}</div>
}
