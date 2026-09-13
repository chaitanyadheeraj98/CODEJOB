// @vitest-environment jsdom
/**
 * §9 E: what Settings says about push delivery.
 *
 * The copy matters as much as the state machine. "Delayed" has to read as
 * *late*, not *lost* - the subscription retains events for seven days, and a
 * word that implied missing mail would send someone hunting for something that
 * is sitting in a queue.
 *
 * The component is rendered rather than reimplemented here. A test that
 * rebuilds the branching it is checking agrees with itself no matter what the
 * component does.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { DELIVERY_TEXT, InboxDeliveryStatus } from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const source = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8')

let container: HTMLDivElement | null = null
let root: Root | null = null

const mount = (status: Parameters<typeof InboxDeliveryStatus>[0]['status']) => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root!.render(<InboxDeliveryStatus status={status} />)
  })
  return container
}

afterEach(() => {
  act(() => root?.unmount())
  container?.remove()
  container = null
  root = null
})

const live = {
  connected: true,
  state: 'connected',
  revoked: false,
  inbox_delivery: 'active' as const,
  watch_expires_at: null,
  last_notification_at: null,
  last_event_processed_at: null,
  consumer_online: true,
  watch_error: '',
}

describe('the delivery status copy', () => {
  it('names all five states', () => {
    expect(Object.keys(DELIVERY_TEXT).sort()).toEqual(
      ['active', 'delayed', 'disabled', 'error', 'registering'],
    )
  })

  it('says delayed mail is late rather than lost', () => {
    // The whole reason this state has its own sentence.
    expect(DELIVERY_TEXT.delayed.hint).toMatch(/nothing is lost/i)
  })

  it('uses caution rather than danger for delayed and for setup errors', () => {
    // Red would contradict the sentence above it.
    expect(DELIVERY_TEXT.delayed.tone).toBe('warn')
    expect(DELIVERY_TEXT.error.tone).toBe('warn')
  })

  it('does not shout about the healthy state', () => {
    // Almost always the current state. A permanent green badge stops carrying
    // information within a day.
    expect(DELIVERY_TEXT.active.label).toBe('Live')
    expect(DELIVERY_TEXT.active.tone).toBe('ok')
  })

  it('names no cloud resource anywhere in the copy', () => {
    const blob = JSON.stringify(DELIVERY_TEXT)
    for (const leaked of ['projects/', 'subscriptions/', 'historyId', 'service account']) {
      expect(blob).not.toContain(leaked)
    }
  })
})

describe('the status line', () => {
  it('shows Live for an active watch with a live consumer', () => {
    expect(mount(live).textContent).toContain('Live')
  })

  it('shows Delayed, and says nothing is lost', () => {
    const node = mount({ ...live, inbox_delivery: 'delayed', consumer_online: false })

    expect(node.textContent).toContain('Delayed')
    expect(node.textContent).toMatch(/nothing is lost/i)
  })

  it('a credential problem outranks whatever the watch says', () => {
    // No amount of delivery setup helps a connection that needs reconnecting,
    // and "Delayed" would send someone looking in the wrong place.
    const node = mount({ ...live, inbox_delivery: 'delayed', revoked: true })

    expect(node.textContent).toContain('Reconnect Gmail')
    expect(node.textContent).not.toContain('Delayed')
  })

  it('announces itself to assistive technology', () => {
    expect(mount(live).querySelector('[role="status"]')).not.toBeNull()
  })

  it('renders nothing before the status has loaded', () => {
    // Rather than flashing "Off" on every page load, which is a state the user
    // would reasonably act on.
    expect(mount(null).textContent).toBe('')
  })

  it('carries the tone as a class so the stylesheet owns the colour', () => {
    expect(mount(live).querySelector('.deliveryStatus--ok')).not.toBeNull()
  })
})

describe('the Settings copy', () => {
  it('no longer claims to poll Gmail on an interval', () => {
    expect(source).not.toMatch(/Checks unread Gmail on the existing polling interval/)
  })

  it('says changes arrive through Pub/Sub', () => {
    expect(source).toContain('Receives Gmail changes through Pub/Sub')
  })

  it('renames the inbox refresh control so it does not promise a scan', () => {
    // Under push it rereads stored rows. "Refresh" implied it went and looked.
    expect(source).toContain('title="Reload inbox"')
    expect(source).toContain('aria-label="Reload inbox"')
  })
})

describe('the unread reply count', () => {
  it('no longer hangs off Sync Now', () => {
    // It counts replies waiting in the Inbox; Sync Now starts a candidate
    // import. Side by side, the number read as work that button would do.
    expect(source).not.toContain('liveReplyBadge')
    expect(source).not.toContain('syncNowWrap')
  })

  it('no longer hedges a number that is now exact', () => {
    // The old caveat described a Gmail unread query. Under push delivery the
    // count is replies already captured and stored.
    expect(source).not.toContain('not confirmed recruiter replies')
    expect(source).not.toContain('unread in Primary inbox')
  })

  it("feeds the Inbox nav from the server's total, not the loaded page", () => {
    // The local reduce counted only the conversations in memory and read zero
    // on every screen that had not opened the Inbox - Settings showed 0 while
    // the Inbox showed 11.
    expect(source).toContain('const inboxUnreadCount = liveReplyStatus?.count ?? 0')
    expect(source).not.toMatch(/inboxUnreadCount = inboxConversations\.reduce/)
  })

  it('re-asks after a conversation is read', () => {
    // The badge would otherwise sit one higher for up to fifteen seconds after
    // the row it counted has visibly gone grey.
    const readBlock = source.slice(source.indexOf('/read`'), source.indexOf('/read`') + 700)
    expect(readBlock).toContain('loadLiveReplyStatus()')
  })
})
