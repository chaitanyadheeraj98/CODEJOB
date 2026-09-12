import { useState } from 'react'

export default function AccountControls({ apiBase, onDeactivated }: {
  apiBase: string
  onDeactivated: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const deactivate = async () => {
    if (!window.confirm('Deactivate this account now? Access and connected credentials will be removed immediately. Your remaining data will be permanently deleted after 30 days.')) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${apiBase}/account/deactivate`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Unable to deactivate the account.')
      onDeactivated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to deactivate the account.')
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>Account</h2>
      <p className="subtle">Deactivation ends access and removes connected credentials now. An administrator can restore the account for 30 days; remaining data is then permanently deleted.</p>
      <button type="button" className="dangerButton" disabled={busy} onClick={() => void deactivate()}>
        {busy ? 'Deactivating...' : 'Deactivate account'}
      </button>
      {error ? <p className="dangerText" role="alert">{error}</p> : null}
    </section>
  )
}
