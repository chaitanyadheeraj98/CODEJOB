import { useEffect, useState } from 'react'

import { startGoogleLogin } from '../features/auth/api'
import { consumeDeletionIntent, forgetDeletionIntent, rememberDeletionIntent } from './deletionIntent'

type Busy = '' | 'export' | 'deactivate' | 'delete'

export default function AccountControls({ apiBase, accountEmail, onDeactivated }: {
  apiBase: string
  accountEmail?: string
  onDeactivated: () => void
}) {
  const [busy, setBusy] = useState<Busy>('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')

  // Coming back from Google. The intent only reopens this screen - the typed
  // confirmation is deliberately not restored, because the point of typing it
  // is that someone typed it just now.
  useEffect(() => {
    if (consumeDeletionIntent()) setConfirming(true)
  }, [])

  // Export sits first, and stays enabled while nothing else is running,
  // because deactivation revokes the session that this call needs. Afterwards
  // there is no way back in to ask for the data.
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

  const cancelDeletion = () => {
    forgetDeletionIntent()
    setConfirming(false)
    setTyped('')
    setError('')
  }

  const requestDeletion = async () => {
    setBusy('delete')
    setError('')
    try {
      const response = await fetch(`${apiBase}/account`, {
        method: 'DELETE',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_email: typed }),
      })

      if (response.status === 401) {
        const detail = await response.json().catch(() => ({}))
        if (detail?.detail === 'reauthentication_required') {
          // Remember *before* navigating: once the redirect happens this
          // component is gone and there is no later chance to record it.
          rememberDeletionIntent()
          window.location.href = await startGoogleLogin(apiBase, { reauth: true })
          return
        }
      }
      if (!response.ok) {
        throw new Error(
          response.status === 400
            ? 'That did not match this account’s email address.'
            : 'Unable to request deletion.',
        )
      }
      forgetDeletionIntent()
      onDeactivated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to request deletion.')
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

      {confirming ? (
        <div className="confirmDeletion">
          <p className="subtle">
            Type <strong>{accountEmail || 'your account email address'}</strong> to confirm. Your
            data is permanently deleted after 30 days; until then an administrator can restore it.
          </p>
          <label htmlFor="confirm-delete-email">Email address</label>
          <input
            id="confirm-delete-email"
            type="email"
            value={typed}
            autoComplete="off"
            onChange={(event) => setTyped(event.target.value)}
          />
          <button type="button" className="dangerButton" disabled={busy !== '' || !typed}
                  onClick={() => void requestDeletion()}>
            {busy === 'delete' ? 'Requesting...' : 'Permanently delete my account'}
          </button>
          <button type="button" disabled={busy !== ''} onClick={cancelDeletion}>Cancel</button>
        </div>
      ) : (
        <>
          <p className="subtle">Deleting removes your account and all of its data permanently. You will be asked to sign in again first.</p>
          <button type="button" className="dangerButton" disabled={busy !== ''}
                  onClick={() => { setError(''); setConfirming(true) }}>
            Delete account
          </button>
        </>
      )}

      {error ? <p className="dangerText" role="alert">{error}</p> : null}
    </section>
  )
}
