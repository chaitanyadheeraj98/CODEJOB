// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import Confidence from './Confidence'
import type { ConfidenceLevel } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('Confidence', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (level: ConfidenceLevel) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<Confidence level={level} />))
    return container
  }

  it.each([
    ['confirmed', 'Confirmed', 'chatConfidenceConfirmed'],
    ['likely', 'Likely', 'chatConfidenceLikely'],
    ['possible', 'Possible', 'chatConfidencePossible'],
  ])('renders %s with its own class', (level, text, className) => {
    const el = render(level as ConfidenceLevel)

    expect(el.textContent).toBe(text)
    expect(el.querySelector(`.${className}`)).not.toBeNull()
  })

  // Confirmed must not share a class with the two inferred levels: the visual
  // distinction between a recorded fact and an inference is a product rule.
  it('gives confirmed a class no inferred level uses', () => {
    const confirmed = render('confirmed').querySelector('.chatConfidence')?.className
    act(() => root?.unmount())
    container?.remove()
    const likely = render('likely').querySelector('.chatConfidence')?.className

    expect(confirmed).not.toBe(likely)
  })

  it('renders nothing for a level this build does not know', () => {
    const el = render('provisional' as ConfidenceLevel)

    expect(el.textContent).toBe('')
    expect(el.querySelector('.chatConfidence')).toBeNull()
  })
})
