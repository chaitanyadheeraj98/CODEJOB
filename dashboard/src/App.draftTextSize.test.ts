import { describe, expect, it } from 'vitest'

import {
  DRAFT_TEXT_SIZE_OPTIONS,
  draftTextSizeToPreviewStyle,
  draftToPreviewHtml,
  normalizeDraftTextSize,
} from './App'

describe('draft text size helpers', () => {
  it('exposes the gmail-style size options', () => {
    expect(DRAFT_TEXT_SIZE_OPTIONS).toEqual(['small', 'normal', 'large', 'huge'])
  })

  it('normalizes unsupported values back to normal', () => {
    expect(normalizeDraftTextSize('Large')).toBe('large')
    expect(normalizeDraftTextSize('gigantic')).toBe('normal')
  })

  it('maps preview sizes to stable font styles', () => {
    expect(draftTextSizeToPreviewStyle('small')).toEqual({ fontSize: '12px', lineHeight: '1.5' })
    expect(draftTextSizeToPreviewStyle('huge')).toEqual({ fontSize: '28px', lineHeight: '1.4' })
  })

  it('keeps preview html formatting unchanged for content blocks', () => {
    const html = draftToPreviewHtml('Hello **Java**\n\n- Spring')
    expect(html).toContain('<strong>Java</strong>')
    expect(html).toContain('<ul><li>Spring</li></ul>')
  })
})
