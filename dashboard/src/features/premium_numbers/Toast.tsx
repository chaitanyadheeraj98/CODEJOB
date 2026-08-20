import { useCallback, useEffect, useState } from 'react'

const TOAST_DURATION_MS = 2500

export function useToast() {
  const [message, setMessage] = useState<string | null>(null)
  const show = useCallback((text: string) => setMessage(text), [])
  const clear = useCallback(() => setMessage(null), [])
  return { message, show, clear }
}

type ToastHostProps = {
  message: string | null
  onDismiss: () => void
}

export function ToastHost({ message, onDismiss }: ToastHostProps) {
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(onDismiss, TOAST_DURATION_MS)
    return () => window.clearTimeout(timer)
  }, [message, onDismiss])

  if (!message) return null
  return <div className="actionToast" role="status">{message}</div>
}
