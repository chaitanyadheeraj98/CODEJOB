import type { JourneyAction } from './journey'

export default function JourneyPanel({ action, onClose }: { action: JourneyAction; onClose: () => void }) {
  return (
    <div className="detailPanelOverlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detailPanel journeyPanel" role="dialog" aria-modal="true" aria-label={action.label}>
        <header className="detailPanelHeader">
          <div>
            <p className="detailPanelEyebrow">Application journey</p>
            <h3>{action.label}</h3>
          </div>
          <button type="button" className="iconBtn" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="detailPanelBody"><p className="subtle">Choose the action details here.</p></div>
      </div>
    </div>
  )
}
