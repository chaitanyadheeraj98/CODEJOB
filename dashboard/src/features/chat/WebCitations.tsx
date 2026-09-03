import { safeHref } from './markdown'
import type { WebResultsData } from './renderers'

type Props = { data: WebResultsData; surface: 'page' | 'compact' }

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

// Visibly separate from internal data: its own container, an explicit "From the
// web" label, and nothing borrowed from .candidateTable. A search result that
// looks like a stored record is a claim the product did not make.
export default function WebCitations({ data, surface }: Props) {
  if (!data.results.length) {
    return (
      <div className="chatWebResults">
        <p className="chatWebLabel">From the web</p>
        <p className="subtle">No results for "{data.query}".</p>
      </div>
    )
  }

  return (
    <div className="chatWebResults">
      <p className="chatWebLabel">From the web — external sources, not your data</p>
      <ol className="chatWebList">
        {data.results.map((result, index) => {
          const href = safeHref(result.url)
          const host = hostname(result.url)
          // A result with no title falls back to its hostname, never to the
          // snippet - attacker-authored text must not become the link label.
          const title = result.title || host
          return (
            <li key={`${result.url}-${index}`}>
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer">{title}</a>
              ) : (
                // An unsafe scheme renders as text with no anchor at all.
                <span>{title}</span>
              )}
              <span className="chatWebHost"> {host}</span>
              {/* Plain text, never renderMarkdownLite: running attacker text
                  through the markdown renderer would let a search result mint
                  links inside the assistant's own answer. */}
              {surface === 'page' && result.snippet ? (
                <p className="chatWebSnippet">{result.snippet}</p>
              ) : null}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
