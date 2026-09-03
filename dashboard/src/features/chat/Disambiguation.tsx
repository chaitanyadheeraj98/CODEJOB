import { useState } from 'react'

import { useChat } from './chatContext'
import type { DisambiguationData } from './renderers'

type Props = { data: DisambiguationData; surface: 'page' | 'compact' }

const NAVIGABLE: Record<string, { page: string; tab: string | null; filterKey: string }> = {
  contact: { page: 'premium_numbers', tab: 'inventory', filterKey: 'q' },
  opportunity: { page: 'premium_numbers', tab: 'opportunities', filterKey: 'q' },
}

// A render handler, not a proposal. The obvious design - the model offers, the
// user picks, the model continues - cannot work here: tool results are never
// replayed into history, so the model cannot see what it offered. The client
// builds the follow-on from the chosen id instead, exactly as CandidateTable
// builds its approve proposal.
export default function Disambiguation({ data, surface }: Props) {
  const chat = useChat()
  const [chosen, setChosen] = useState<{ id: number; label: string } | null>(null)

  // The widget is narrow and a mis-tap here picks the wrong person, so the
  // compact surface shows the options without offering the choice.
  if (surface === 'compact') {
    return (
      <div className="chatDisambiguation">
        <p>Several {data.kind}s match "{data.query}":</p>
        <ul>
          {data.options.map((option) => (
            <li key={option.id}>{option.label}{option.detail ? ` — ${option.detail}` : ''}</li>
          ))}
        </ul>
        <p className="subtle">Open the Assistant page to choose one.</p>
      </div>
    )
  }

  const target = NAVIGABLE[data.kind]

  return (
    <div className="chatDisambiguation">
      <p>Several {data.kind}s match "{data.query}". Which one?</p>
      <ul className="chatDisambiguationOptions">
        {data.options.map((option) => (
          <li key={option.id}>
            <button
              type="button"
              className={chosen?.id === option.id ? 'chosen' : ''}
              onClick={() => {
                setChosen({ id: option.id, label: option.label })
                if (data.kind === 'candidate') {
                  chat.focusCandidate(option.id)
                } else if (target) {
                  chat.navigateToQueue({
                    page: target.page,
                    tab: target.tab,
                    filters: { [target.filterKey]: option.label },
                  })
                }
              }}
            >
              <strong>{option.label}</strong>
              {option.detail ? <span className="subtle"> {option.detail}</span> : null}
            </button>
          </li>
        ))}
      </ul>
      {data.truncated ? <p className="subtle">More matched than are shown; narrow the name.</p> : null}
      {/* Choosing opens the record rather than sending a message. The model
          never learns which was picked, and does not need to: the user is now
          looking at the right one, and any follow-up action names it. */}
      {chosen ? <p className="subtle">Opened {chosen.label}.</p> : null}
    </div>
  )
}
