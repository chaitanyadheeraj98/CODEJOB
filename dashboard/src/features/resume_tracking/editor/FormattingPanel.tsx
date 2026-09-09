import type { ResumeFormatSpec } from '../types'

type Props = { spec: ResumeFormatSpec; onChange: (spec: ResumeFormatSpec) => void }

// `-?` and NonNullable are both needed: several spec fields are optional, and
// without them `number | undefined` matches neither branch and every key comes
// back as `undefined`. Extract drops that residue from the union.
type KeysOf<T> = Extract<{
  [K in keyof ResumeFormatSpec]-?: NonNullable<ResumeFormatSpec[K]> extends T ? K : never
}[keyof ResumeFormatSpec], string>

type NumberKey = KeysOf<number>
type BoolKey = KeysOf<boolean>

// min, max and step per field, so a spinner cannot walk a margin to 40 inches.
const RANGE: Record<NumberKey, [number, number, number]> = {
  body_font_size: [6, 24, 0.5],
  name_font_size: [6, 48, 0.5],
  heading_font_size: [6, 36, 0.5],
  line_spacing: [0.8, 3, 0.1],
  margin_top_inches: [0, 2, 0.05],
  margin_bottom_inches: [0, 2, 0.05],
  margin_left_inches: [0, 2, 0.05],
  margin_right_inches: [0, 2, 0.05],
  heading_space_before_pt: [0, 48, 1],
  heading_space_after_pt: [0, 48, 1],
  section_spacing_pt: [0, 48, 1],
  skills_divider_inches: [0.25, 4, 0.05],
  skills_row_gap_pt: [0, 48, 1],
  bullet_indent_inches: [0, 2, 0.05],
  bullet_hanging_inches: [0, 2, 0.05],
  environment_gap_pt: [0, 48, 1],
  sidebar_width_inches: [1, 4, 0.1],
}

const FONTS = ['Arial', 'Calibri', 'Times New Roman', 'Georgia', 'Verdana']

/**
 * The layout controls, grouped the way a person changes them.
 *
 * These were one flat grid of sixteen number inputs, which is every field the
 * spec has in the order the type happens to declare them. Nothing about "Top
 * margin" sitting beside "Name size" tells you they are unrelated, so the panel
 * had to be read in full to find one control. Grouping is the whole fix: page,
 * type, margins, headings, skills. Same sixteen numbers, five short lists.
 */
export default function FormattingPanel({ spec, onChange }: Props) {
  const set = <K extends keyof ResumeFormatSpec>(key: K, value: ResumeFormatSpec[K]) => onChange({ ...spec, [key]: value })

  const Num = ({ field, label }: { field: NumberKey; label: string }) => {
    const [min, max, step] = RANGE[field]
    return (
      <label className="resumeFormatField">
        <span>{label}</span>
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={Number(spec[field] ?? 0)}
          onChange={(event) => {
            if (event.target.value && event.target.validity.valid) set(field, Number(event.target.value) as ResumeFormatSpec[NumberKey])
          }}
        />
      </label>
    )
  }

  // A checkbox styled as a switch, not a new control: it keeps the native
  // element, so the label, focus ring and keyboard behaviour are the browser's.
  const Switch = ({ field, label }: { field: BoolKey; label: string }) => (
    <label className="resumeFormatSwitch">
      <input type="checkbox" checked={Boolean(spec[field])} onChange={(event) => set(field, event.target.checked as ResumeFormatSpec[BoolKey])} />
      <span className="resumeSwitchTrack" aria-hidden="true"><span className="resumeSwitchThumb" /></span>
      <span className="resumeSwitchLabel">{label}</span>
    </label>
  )

  return (
    <details className="resumeFormatting" open>
      <summary>Formatting</summary>

      <div className="resumeFormatGroup">
        <h4>Page</h4>
        <div className="resumeFormatRow">
          <label className="resumeFormatField">
            <span>Page size</span>
            <select value={spec.page_size ?? 'LETTER'} onChange={(event) => set('page_size', event.target.value as 'LETTER' | 'A4')}>
              <option value="LETTER">US Letter</option>
              <option value="A4">A4</option>
            </select>
          </label>
          <label className="resumeFormatField">
            <span>Font</span>
            <select value={spec.font_family} onChange={(event) => set('font_family', event.target.value)}>
              {[...new Set([spec.font_family, ...FONTS])].map((font) => <option key={font}>{font}</option>)}
            </select>
          </label>
        </div>
        <Switch field="compact" label="Compact spacing" />
      </div>

      <div className="resumeFormatGroup">
        <h4>Columns</h4>
        <div className="resumeFormatRow">
          <label className="resumeFormatField">
            <span>Layout</span>
            <select value={spec.layout ?? 'single'} onChange={(event) => set('layout', event.target.value as 'single' | 'two-column')}>
              <option value="single">Single column</option>
              <option value="two-column">Two column</option>
            </select>
          </label>
          {spec.layout === 'two-column' ? <Num field="sidebar_width_inches" label="Sidebar (in)" /> : null}
        </div>
        {spec.layout === 'two-column' ? (
          <label className="resumeFormatField">
            <span>Sections in the sidebar</span>
            <input
              value={(spec.sidebar_sections ?? []).join(', ')}
              placeholder="Skills, Certifications, Education Details"
              onChange={(event) => set('sidebar_sections', event.target.value.split(','))}
            />
            {/* Nothing in the markdown says which sections are secondary, and it
                differs by employer, so this is named rather than guessed. */}
            <small className="subtle">
              {(spec.sidebar_sections ?? []).some((name) => name.trim())
                ? 'Headings must match the draft exactly. Anything else stays in the main column.'
                : 'Name at least one heading, or this renders as a single column.'}
            </small>
          </label>
        ) : null}
      </div>

      <div className="resumeFormatGroup">
        <h4>Type</h4>
        <div className="resumeFormatRow">
          <Num field="body_font_size" label="Body (pt)" />
          <Num field="heading_font_size" label="Heading (pt)" />
          <Num field="name_font_size" label="Name (pt)" />
          <Num field="line_spacing" label="Line spacing" />
        </div>
        <Switch field="justify_body" label="Justify body text" />
      </div>

      <div className="resumeFormatGroup">
        <h4>Margins <span className="resumeFormatUnit">inches</span></h4>
        <div className="resumeFormatRow">
          <Num field="margin_top_inches" label="Top" />
          <Num field="margin_bottom_inches" label="Bottom" />
          <Num field="margin_left_inches" label="Left" />
          <Num field="margin_right_inches" label="Right" />
        </div>
      </div>

      <div className="resumeFormatGroup">
        <h4>Headings</h4>
        <div className="resumeFormatRow">
          <Num field="heading_space_before_pt" label="Space before (pt)" />
          <Num field="heading_space_after_pt" label="Space after (pt)" />
          <Num field="section_spacing_pt" label="Section gap (pt)" />
          <label className="resumeFormatField">
            <span>Colour</span>
            <input type="color" value={spec.accent_color || '#171717'} onChange={(event) => set('accent_color', event.target.value)} />
          </label>
        </div>
        <Switch field="heading_bold" label="Bold headings" />
        <Switch field="heading_uppercase" label="Uppercase headings" />
        <label className="resumeFormatField">
          <span>Rules above sections</span>
          <input
            value={spec.rule_before_sections.join(', ')}
            placeholder="Summary, Skills, Experiences"
            onChange={(event) => set('rule_before_sections', event.target.value.split(','))}
          />
        </label>
      </div>

      <div className="resumeFormatGroup">
        <h4>Skills and bullets</h4>
        <div className="resumeFormatRow">
          <Num field="skills_divider_inches" label="Skills divider (in)" />
          <Num field="skills_row_gap_pt" label="Skills row gap (pt)" />
          <Num field="bullet_indent_inches" label="Bullet indent (in)" />
          <Num field="bullet_hanging_inches" label="Bullet hanging (in)" />
          <Num field="environment_gap_pt" label="Environment gap (pt)" />
        </div>
        <Switch field="skills_category_bold" label="Bold skill categories" />
      </div>
    </details>
  )
}
