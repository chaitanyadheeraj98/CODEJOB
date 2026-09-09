import type { ResumeDraftSection } from '../types'

type Props = {
  sections: ResumeDraftSection[]
  stale: boolean
  moving: string
  onSelect: (section: ResumeDraftSection) => void
  onMove?: (section: ResumeDraftSection, direction: 'up' | 'down') => void
}

/** Can this section trade places with a sibling in that direction? */
function canMove(sections: ResumeDraftSection[], section: ResumeDraftSection, direction: 'up' | 'down') {
  const siblings = sections.filter((item) => item.level === section.level)
  const index = siblings.findIndex((item) => item.start === section.start)
  return direction === 'up' ? index > 0 : index >= 0 && index < siblings.length - 1
}

/**
 * Jump list for the draft's headings, and where they are reordered.
 *
 * Compact on purpose: a tailored resume has fifteen or more headings, and at
 * button size that list was taller than the editor beside it.
 *
 * Moving happens here rather than by cutting and pasting in the textarea,
 * because a section is a heading plus everything under it, and the line where
 * that ends is exactly what is easy to get wrong by hand.
 */
export default function SectionOutline({ sections, stale, moving, onSelect, onMove }: Props) {
  return (
    <nav className="resumeSectionOutline" aria-label="Resume sections">
      <div className="resumeSectionOutlineHead">
        <strong>Sections</strong>
        <span className="resumeCount">{sections.length}</span>
      </div>
      {stale ? <small className="subtle">Save the text to refresh this list.</small> : null}
      <div className="resumeSectionOutlineList">
        {sections.map((section) => (
          <div className="resumeSectionRow" key={section.start}>
            <button
              type="button"
              className="resumeSectionJump"
              disabled={stale}
              title={section.heading}
              onClick={() => onSelect(section)}
              // Depth is shown by indent rather than by a second type size: the
              // headings are already short, and shrinking them made the deepest
              // ones the hardest to read.
              style={{ paddingLeft: `${8 + Math.max(0, Math.min(section.level, 6) - 1) * 10}px` }}
            >
              {section.heading}
            </button>
            {onMove ? (
              <span className="resumeSectionMove">
                {(['up', 'down'] as const).map((direction) => (
                  <button
                    key={direction}
                    type="button"
                    // Disabled at the ends rather than hidden, so the row does
                    // not change width as the list is reordered.
                    disabled={stale || !!moving || !canMove(sections, section, direction)}
                    aria-label={`Move ${section.heading} ${direction}`}
                    onClick={() => onMove(section, direction)}
                  >
                    <span className={`resumeCaret ${direction}`} aria-hidden="true" />
                  </button>
                ))}
              </span>
            ) : null}
          </div>
        ))}
        {!sections.length && !stale ? <small className="subtle">No headings yet.</small> : null}
      </div>
    </nav>
  )
}
