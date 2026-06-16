import { describe, expect, it } from 'vitest'

import { appendUniqueReminderMessage } from './reminderMessages'

describe('appendUniqueReminderMessage', () => {
  it('appends each reminder only once by reminder id', () => {
    const seen = new Set([1])
    const messages = [{ role: 'assistant', content: '请按时服药', images: [] }]
    const reminder = { reminder_id: 1, role: 'assistant', content: '请按时服药' }

    const next = appendUniqueReminderMessage({ messages, seenReminderIds: seen, reminder })

    expect(next).toBe(messages)
    expect(next).toHaveLength(1)
    expect(next[0]).toEqual({ role: 'assistant', content: '请按时服药', images: [] })
  })

  it('does not mark reminders seen before server acknowledgement succeeds', () => {
    const seen = new Set()
    const messages = []
    const reminder = { reminder_id: 1, role: 'assistant', content: '请按时服药' }

    const next = appendUniqueReminderMessage({ messages, seenReminderIds: seen, reminder })

    expect(next).toHaveLength(1)
    expect(seen.has(1)).toBe(false)
  })
})
