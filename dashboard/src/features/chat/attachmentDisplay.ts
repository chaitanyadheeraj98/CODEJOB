import type { ChatAttachment } from './types'

// Split from AttachmentChips.tsx so that file exports only components, which is
// what react-refresh/only-export-components wants.
export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.csv']

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export type PendingAttachment =
  | { status: 'uploading'; localId: string; fileName: string }
  | { status: 'failed'; localId: string; fileName: string; error: string }
  | { status: 'ready'; localId: string; fileName: string; attachment: ChatAttachment }
