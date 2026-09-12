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
    <section className="card accountCard">
      <h2>Account</h2>

      <div className="accountSection">
        <h3 className="accountSection__title">Export your data</h3>
        <p className="subtle accountSection__blurb">
          A JSON copy of your applications, conversations and taxonomy. Take it before you go —
          deactivating ends the session this download needs.
        </p>
        <button type="button" disabled={busy !== ''} onClick={() => void exportData()}>
          {busy === 'export' ? 'Preparing download...' : 'Export my data'}
        </button>
      </div>

      <div className="accountSection accountSection--caution">
        <h3 className="accountSection__title">Deactivate</h3>
        <p className="subtle accountSection__blurb">
          Ends access and removes connected credentials now. An administrator can restore the
          account for 30 days; after that the remaining data is permanently deleted.
        </p>
        <button type="button" className="dangerButton" disabled={busy !== ''} onClick={() => void deactivate()}>
          {busy === 'deactivate' ? 'Deactivating...' : 'Deactivate account'}
        </button>
      </div>

      <div className="accountSection accountSection--refusal">
        <h3 className="accountSection__title">Delete permanently</h3>
        {confirming ? (
          <div className="confirmDeletion">
            <p className="accountSection__blurb">
              To confirm, type <code className="confirmDeletion__literal">{accountEmail || 'your account email address'}</code> below.
              You will be asked to sign in again before this takes effect.
            </p>
            <label className="confirmDeletion__label" htmlFor="confirm-delete-email">
              Email address
            </label>
            <input
              id="confirm-delete-email"
              className="confirmDeletion__input"
              type="email"
              value={typed}
              autoComplete="off"
              spellCheck={false}
              placeholder={accountEmail || ''}
              onChange={(event) => setTyped(event.target.value)}
            />
            <div className="confirmDeletion__actions">
              <button type="button" className="dangerButtonSolid" disabled={busy !== '' || !typed}
                      onClick={() => void requestDeletion()}>
                {busy === 'delete' ? 'Requesting...' : 'Permanently delete my account'}
              </button>
              <button type="button" className="confirmDeletion__cancel" disabled={busy !== ''}
                      onClick={cancelDeletion}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <p className="subtle accountSection__blurb">
              Removes your account and everything in it. Your data stays recoverable for 30 days,
              then it is gone for good.
            </p>
            <button type="button" className="dangerButton" disabled={busy !== ''}
                    onClick={() => { setError(''); setConfirming(true) }}>
              Delete account
            </button>
          </>
        )}
      </div>

      {error ? <p className="dangerText accountCard__error" role="alert">{error}</p> : null}
    </section>
  )
}
