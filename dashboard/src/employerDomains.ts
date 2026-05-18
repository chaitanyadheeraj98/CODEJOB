const DOMAIN_RE = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$/i

export function normalizeDomain(raw: string): string {
  return raw.trim().toLowerCase()
}

export function isValidDomain(domain: string): boolean {
  return DOMAIN_RE.test(domain)
}

export function addEmployerDomain(existing: string[], raw: string): {
  next: string[]
  added: boolean
  error: string | null
} {
  const normalized = normalizeDomain(raw)
  if (!normalized) {
    return { next: existing, added: false, error: null }
  }
  if (!isValidDomain(normalized)) {
    return { next: existing, added: false, error: "Invalid domain format" }
  }
  const seen = new Set(existing.map((item) => normalizeDomain(item)))
  if (seen.has(normalized)) {
    return { next: existing, added: false, error: null }
  }
  return { next: [...existing, normalized], added: true, error: null }
}

export function removeEmployerDomain(existing: string[], target: string): string[] {
  const normalizedTarget = normalizeDomain(target)
  return existing.filter((item) => normalizeDomain(item) !== normalizedTarget)
}
