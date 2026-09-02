import { formatBytes, type PendingAttachment } from './attachmentDisplay'
import type { ChatAttachment } from './types'

type AttachmentChipsProps = {
  pending: PendingAttachment[]
  onRemove: (localId: string) => void
}

export default function AttachmentChips({ pending, onRemove }: AttachmentChipsProps) {
  if (!pending.length) return null
  return (
    <ul className="attachmentChips">
      {pending.map((item) => (
        <li key={item.localId} className={`attachmentChip ${item.status}`}>
          <span className="attachmentChipName">{item.fileName}</span>
          {item.status === 'uploading' ? <span className="attachmentChipNote">Uploading…</span> : null}
          {item.status === 'failed' ? <span className="attachmentChipNote">{item.error}</span> : null}
          {/* An extraction failure is a warning, not a rejection: the file is
              attached and downloadable, the assistant just cannot read it. */}
          {item.status === 'ready' && item.attachment.extraction_error ? (
            <span className="attachmentChipNote">Attached, but the text could not be read</span>
          ) : null}
          {item.status === 'ready' && !item.attachment.extraction_error ? (
            <span className="attachmentChipNote">{formatBytes(item.attachment.byte_size)}</span>
          ) : null}
          <button type="button" onClick={() => onRemove(item.localId)} aria-label={`Remove ${item.fileName}`}>×</button>
        </li>
      ))}
    </ul>
  )
}

export function SentAttachmentChips({ apiBase, attachments }: { apiBase: string; attachments: ChatAttachment[] }) {
  if (!attachments.length) return null
  return (
    <ul className="attachmentChips sent">
      {attachments.map((attachment) => (
        <li key={attachment.id} className="attachmentChip ready">
          <a href={`${apiBase}/chat/attachments/${attachment.id}/download`} download>
            {attachment.file_name}
          </a>
          <span className="attachmentChipNote">{formatBytes(attachment.byte_size)}</span>
        </li>
      ))}
    </ul>
  )
}
