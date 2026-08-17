const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/

export function addCcEmail(existing: string[], raw: string): {
  next: string[]
  added: boolean
  error: string | null
} {
  const email = raw.trim().toLowerCase()
  if (!email) return { next: existing, added: false, error: null }
  if (!EMAIL_RE.test(email)) return { next: existing, added: false, error: 'Invalid email address' }
  if (existing.some((value) => value.trim().toLowerCase() === email)) {
    return { next: existing, added: false, error: null }
  }
  return { next: [...existing, email], added: true, error: null }
}

export function removeCcEmail(existing: string[], target: string): string[] {
  const email = target.trim().toLowerCase()
  return existing.filter((value) => value.trim().toLowerCase() !== email)
}
