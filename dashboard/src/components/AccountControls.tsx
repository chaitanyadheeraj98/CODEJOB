import { useState } from 'react'

type Busy = '' | 'export' | 'deactivate'

export default function AccountControls({ apiBase, onDeactivated }: {
  apiBase: string
  onDeactivated: () => void
}) {
  const [busy, setBusy] = useState<Busy>('')
  const [error, setError] = useState('')

  // Export sits above deactivation, and stays enabled while nothing else is
  // running, because deactivation revokes the session that this call needs.
  // Afterwards there is no way back in to ask for the data.
  const exportData = async () => {
    setBusy('export')
    setError('')
    try {
      const response = await fetch(`${apiBase}/account/export`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Unable to export your data.')
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `codejob-export-${new Date().toISOString().slice(0, 10)}.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to export your data.')
    } finally {
      setBusy('')
    }
  }

  const deactivate = async () => {
    if (!window.confirm('Deactivate this account now? Access and connected credentials will be removed immediately. Your remaining data will be permanently deleted after 30 days.')) return
    setBusy('deactivate')
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
      setBusy('')
    }
  }

  return (
    <section className="card">
      <h2>Account</h2>
      <p className="subtle">Download a copy of your applications, conversations and taxonomy before you go — deactivation ends the session this download needs.</p>
      <button type="button" disabled={busy !== ''} onClick={() => void exportData()}>
        {busy === 'export' ? 'Preparing download...' : 'Export my data'}
      </button>
      <p className="subtle">Deactivation ends access and removes connected credentials now. An administrator can restore the account for 30 days; remaining data is then permanently deleted.</p>
      <button type="button" className="dangerButton" disabled={busy !== ''} onClick={() => void deactivate()}>
        {busy === 'deactivate' ? 'Deactivating...' : 'Deactivate account'}
      </button>
      {error ? <p className="dangerText" role="alert">{error}</p> : null}
    </section>
  )
}
