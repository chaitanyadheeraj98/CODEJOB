// The Settings path composes its entry client-side, because it has no tool call
// to do it for it. That duplicates compose_entry and compose_append from
// backend/app/services/candidate_profile_service.py across two languages; the
// parity test in AddProfileEntry.test.tsx pins the two together character for
// character. The alternative - a preview endpoint whose only caller would be one
// panel - is worth taking only if these ever drift.
//
// Split from AddProfileEntry.tsx so that file exports only a component, which is
// what react-refresh/only-export-components wants.

// The canonical labels, mirroring PROFILE_FIELDS on the server. The registry,
// not the person and not the model, chooses the label: <user_profile> is the one
// block the assistant is told to believe, so its vocabulary is fixed.
export const PROFILE_FIELD_LABELS = [
  'Work Authorization',
  'Notice period',
  'Current location',
  'Willing to relocate',
  'Rate',
  'Phone',
  'Email',
  'Passport',
  'Total experience',
  'Availability',
  'Employment type',
  'LinkedIn',
] as const

export const SAVED_HEADING = '## Saved from chat'

export function composeEntry(field: string, value: string, verbatim: boolean): string {
  const cleaned = value.trim()
  return verbatim ? `- ${field} (in the user's words): "${cleaned}"` : `- ${field}: ${cleaned}`
}

// Mirrors entry_field() on the server. Every line the Saved-from-chat block can
// hold was composed by composeEntry with a canonical label, so matching labels
// is enough here - the server also accepts aliases, which no stored line uses.
export function entryField(entry: string): string | null {
  let line = entry.trim()
  if (line.startsWith('- ')) line = line.slice(2)
  let head = line.split(':', 1)[0].trim()
  if (head.includes('(')) head = head.split('(', 1)[0].trim()
  const needle = head.toLowerCase()
  return PROFILE_FIELD_LABELS.find((label) => label.toLowerCase() === needle) ?? null
}

// Mirrors plan_append(): the document, and the entries it replaces. One field
// holds one line, so re-stating a field the block already carries is an update.
// Leaving both would put a contradiction inside the one block the assistant is
// told to believe, and it has no way to tell which half is current.
export function planAppend(existing: string, entry: string): { combined: string; replaces: string[] } {
  const current = existing.trim()
  const lines = current.split('\n')
  const headingAt = lines.findIndex((line) => line.trim() === SAVED_HEADING)
  if (headingAt < 0) return { combined: `${current}\n\n${SAVED_HEADING}\n${entry}\n`, replaces: [] }
  // Preserve everything after the section: a profile whose author keeps their
  // own sections below ours must come back with those intact and in order.
  let end = lines.length
  for (let index = headingAt + 1; index < lines.length; index += 1) {
    if (lines[index].startsWith('#')) {
      end = index
      break
    }
  }
  while (end > headingAt + 1 && !lines[end - 1].trim()) end -= 1

  const start = headingAt + 1
  const field = entryField(entry)
  let body = lines.slice(start, end)
  const matches = field ? body.flatMap((row, index) => (entryField(row) === field ? [index] : [])) : []
  let replaces: string[] = []
  if (matches.length) {
    replaces = matches.map((index) => body[index])
    // Rewrite the first and drop any others: a profile written before this rule
    // could already hold several lines for one field, and leaving the extras
    // would be leaving the contradiction.
    const drop = new Set(matches.slice(1))
    body = body.filter((_, index) => !drop.has(index))
    body[matches[0]] = entry
  } else {
    body = [...body, entry]
  }
  const combined = [...lines.slice(0, start), ...body, ...lines.slice(end)].join('\n')
  return { combined: combined.endsWith('\n') ? combined : `${combined}\n`, replaces }
}

export function composeAppend(existing: string, entry: string): string {
  return planAppend(existing, entry).combined
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}
