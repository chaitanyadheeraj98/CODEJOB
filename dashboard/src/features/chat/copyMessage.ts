// Copy a message so it survives the paste.
//
// Two flavours go on the clipboard at once: the markdown the assistant actually
// wrote as text/plain, and the rendered DOM as text/html. Pasting into a plain
// editor keeps the markdown; pasting into Word, Docs or an email keeps the
// headings, bold, links and tables. Sending only one loses whichever the
// destination wanted.
//
// `text/html` is read from the element that is already on screen rather than
// re-rendered, so what lands in the paste is by construction what the user was
// looking at.
export async function copyMessage(plainText: string, html?: string): Promise<void> {
  const clipboard = navigator.clipboard
  if (!clipboard) throw new Error('This browser will not let the page copy for you.')

  if (html && typeof ClipboardItem !== 'undefined' && clipboard.write) {
    try {
      await clipboard.write([
        new ClipboardItem({
          'text/plain': new Blob([plainText], { type: 'text/plain' }),
          'text/html': new Blob([html], { type: 'text/html' }),
        }),
      ])
      return
    } catch {
      // Firefox gates ClipboardItem behind a flag and Safari rejects a write
      // that is not inside the gesture. Falling through loses the formatting,
      // which is a far better outcome than the button doing nothing.
    }
  }
  await clipboard.writeText(plainText)
}
