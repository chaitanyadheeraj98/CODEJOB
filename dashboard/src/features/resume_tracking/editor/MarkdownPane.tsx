type Props = { text: string; onChange: (value: string) => void }
export default function MarkdownPane({ text, onChange }: Props) {
return (<>
                <label className="resumeField">
                  <span className="visuallyHidden">Draft text</span>
                  <textarea
                    className="resumeEditorTextarea"
                    rows={24}
                    spellCheck
                    value={text}
                    placeholder={'# Your Name\nCity | Phone | Email\n\n## Summary\n...'}
                    onChange={(event) => onChange(event.target.value)}
                  />
                </label>
                <p className="subtle">
                  Markdown: <code>#</code> for the name, <code>##</code> for sections, <code>-</code> for bullets,
                  <code>**bold**</code> for keywords, and a pipe table for skills.
                </p>
</>)
}
