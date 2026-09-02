import type { ReactNode } from 'react'


function renderInline(line: string) {
  return line.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/).map((chunk, i) => {
    if (chunk.startsWith('`') && chunk.endsWith('`') && chunk.length > 1) return <code key={i}>{chunk.slice(1, -1)}</code>
    if (chunk.startsWith('**') && chunk.endsWith('**')) return <strong key={i}>{chunk.slice(2, -2)}</strong>
    if (chunk.startsWith('*') && chunk.endsWith('*') && chunk.length > 1) return <em key={i}>{chunk.slice(1, -1)}</em>
    return chunk
  })
}

const TABLE_SEPARATOR_ROW = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/

function splitTableRow(line: string): string[] {
  return line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

// ponytail: headings/bold/italic/bullets/numbered lists/tables/inline code only, not full markdown. Swap for a real parser if fenced code blocks or links show up.
export function renderMarkdownLite(text: string) {
  const blocks: ReactNode[] = []
  let paragraph: string[] = []
  let list: string[] = []
  let listOrdered = false

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(
      <p key={blocks.length}>
        {paragraph.map((line, i) => (
          <span key={i}>
            {renderInline(line)}
            {i < paragraph.length - 1 ? <br /> : null}
          </span>
        ))}
      </p>,
    )
    paragraph = []
  }
  const flushList = () => {
    if (!list.length) return
    const ListTag = listOrdered ? 'ol' : 'ul'
    blocks.push(
      <ListTag key={blocks.length}>
        {list.map((line, i) => (
          <li key={i}>{renderInline(line)}</li>
        ))}
      </ListTag>,
    )
    list = []
  }

  const lines = text.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) {
      flushParagraph()
      flushList()
      i += 1
      continue
    }
    const nextLine = (lines[i + 1] ?? '').trim()
    const isTable = line.startsWith('|') && line.endsWith('|') && TABLE_SEPARATOR_ROW.test(nextLine)
    if (isTable) {
      flushParagraph()
      flushList()
      const header = splitTableRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i].trim()))
        i += 1
      }
      blocks.push(
        <table key={blocks.length}>
          <thead>
            <tr>{header.map((cell, c) => <th key={c}>{renderInline(cell)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>{row.map((cell, c) => <td key={c}>{renderInline(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)/)
    const orderedItem = line.match(/^\d+\.\s+(.*)/)
    const bulletItem = line.match(/^[-*]\s+(.*)/)
    if (heading) {
      flushParagraph()
      flushList()
      const level = Math.min(heading[1].length, 3)
      const headingContent = renderInline(heading[2])
      blocks.push(
        level === 1 ? (
          <h4 key={blocks.length}>{headingContent}</h4>
        ) : level === 2 ? (
          <h5 key={blocks.length}>{headingContent}</h5>
        ) : (
          <h6 key={blocks.length}>{headingContent}</h6>
        ),
      )
    } else if (orderedItem || bulletItem) {
      flushParagraph()
      const ordered = Boolean(orderedItem)
      if (list.length && listOrdered !== ordered) flushList()
      listOrdered = ordered
      list.push(ordered ? orderedItem![1] : bulletItem![1])
    } else {
      flushList()
      paragraph.push(line)
    }
    i += 1
  }
  flushParagraph()
  flushList()
  return blocks
}
